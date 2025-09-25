#!/usr/bin/env python3
"""DNS 域名可訪問性與 SSL 證書檢測工具。

讀取輸入 CSV，依序檢查每個 DNS 記錄的 80/443 連線可用性，並在 443 端口上
擷取證書資訊與計算剩餘有效天數。

預期輸入 CSV 欄位至少包含：
- project_id
- domain_name
- record_name
- record_type
- record_value

輸出 CSV 會保留原始欄位並新增檢測結果。
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import socket
import ssl
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


DEFAULT_TIMEOUT = 5.0
DEFAULT_THREADS = 10
PROGRESS_INTERVAL = 25


@dataclass
class PortCheckResult:
    accessible: bool
    response_time: Optional[float]
    error: str | None = None


@dataclass
class CertificateResult:
    has_certificate: bool = False
    expiry_iso: Optional[str] = None
    days_until_expiry: Optional[float] = None
    issuer: Optional[str] = None
    subject: Optional[str] = None
    error: Optional[str] = None
    self_signed: Optional[bool] = None
    verify_code: Optional[int] = None
    verify_error: Optional[str] = None
    trust_status: Optional[str] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="檢測 DNS 記錄的 80/443 端口可訪問性與 SSL 證書到期日期"
    )
    parser.add_argument(
        "--input-file",
        "-i",
        required=True,
        help="輸入 CSV 檔案路徑",
    )
    parser.add_argument(
        "--output-file",
        "-o",
        help="輸出 CSV 檔案路徑（預設為輸入檔名加上 _accessibility 後綴）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"連線逾時秒數（預設 {DEFAULT_TIMEOUT} 秒）",
    )
    parser.add_argument(
        "--threads",
        "-t",
        type=int,
        default=DEFAULT_THREADS,
        help=f"並發執行緒數量（預設 {DEFAULT_THREADS}）",
    )
    return parser.parse_args()


def load_records(csv_path: Path) -> List[Dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def sanitize_hostname(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().rstrip(".")


def is_ip_address(value: str | None) -> bool:
    if not value:
        return False
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def check_tcp_port(host: str, port: int, timeout: float) -> PortCheckResult:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = round(time.perf_counter() - start, 3)
            return PortCheckResult(True, elapsed)
    except Exception as exc:
        return PortCheckResult(False, None, str(exc))


def check_https(
    connect_host: str,
    server_hostname: str,
    timeout: float,
) -> Tuple[PortCheckResult, CertificateResult]:
    start = time.perf_counter()
    context = ssl.create_default_context()
    # 允許擷取證書即使其無法通過驗證
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    cert_result = CertificateResult()

    try:
        with socket.create_connection((connect_host, 443), timeout=timeout) as raw_sock:
            raw_sock.settimeout(timeout)
            with context.wrap_socket(
                raw_sock,
                server_hostname=server_hostname or connect_host,
            ) as ssl_sock:
                elapsed = round(time.perf_counter() - start, 3)
                port_result = PortCheckResult(True, elapsed)
                cert = extract_certificate_metadata(ssl_sock)
                if cert:
                    cert_result.has_certificate = True
                    cert_result.subject = format_dn(cert.get("subject"))
                    cert_result.issuer = format_dn(cert.get("issuer"))
                    not_after = cert.get("notAfter")
                    if not_after:
                        try:
                            expiry_dt = datetime.strptime(
                                not_after, "%b %d %H:%M:%S %Y %Z"
                            ).replace(tzinfo=timezone.utc)
                            cert_result.expiry_iso = expiry_dt.isoformat()
                            days_left = (expiry_dt - datetime.now(timezone.utc)).total_seconds() / 86400
                            cert_result.days_until_expiry = round(days_left, 2)
                            cert_result.error = None
                        except Exception as exc:  # pragma: no cover - 格式異常罕見
                            cert_result.error = f"無法解析證書到期日: {exc}"
                if cert:
                    verify_code, verify_error = probe_certificate_trust(
                        connect_host,
                        server_hostname or connect_host,
                        timeout,
                    )
                    cert_result.verify_code = verify_code
                    cert_result.verify_error = verify_error
                    if verify_code == 0 and not verify_error:
                        cert_result.trust_status = "trusted"
                    elif verify_code is not None or verify_error:
                        cert_result.trust_status = "untrusted"
                    cert_result.self_signed = determine_self_signed(cert_result)
                if not cert_result.has_certificate and not cert_result.error:
                    cert_result.error = "未取得對端證書"
                return port_result, cert_result
    except Exception as exc:
        elapsed = None
        if isinstance(exc, ssl.SSLError) and isinstance(exc.args, tuple) and exc.args:
            cert_result.error = f"SSL 錯誤: {exc.args[0]}"
        else:
            cert_result.error = str(exc)
        port_result = PortCheckResult(False, elapsed, cert_result.error)
        return port_result, cert_result


def format_dn(rdns: Iterable[Tuple[Tuple[str, str], ...]] | None) -> str | None:
    if not rdns:
        return None
    parts: List[str] = []
    for rdn in rdns:
        for key, value in rdn:
            parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else None


def extract_certificate_metadata(ssl_sock: ssl.SSLSocket) -> Optional[Dict[str, object]]:
    try:
        der_bytes = ssl_sock.getpeercert(binary_form=True)
    except Exception:
        return None

    if not der_bytes:
        return None

    tmp_path: Optional[Path] = None
    try:
        pem = ssl.DER_cert_to_PEM_cert(der_bytes)
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write(pem)
            tmp.flush()
            tmp_path = Path(tmp.name)
        decoded = ssl._ssl._test_decode_cert(str(tmp_path))  # type: ignore[attr-defined]
        return decoded
    except Exception:
        return None
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def probe_certificate_trust(
    connect_host: str,
    server_hostname: str,
    timeout: float,
) -> Tuple[Optional[int], Optional[str]]:
    context = ssl.create_default_context()
    context.check_hostname = False
    try:
        with socket.create_connection((connect_host, 443), timeout=timeout) as raw_sock:
            raw_sock.settimeout(timeout)
            with context.wrap_socket(
                raw_sock, server_hostname=server_hostname
            ):
                return 0, None
    except ssl.SSLCertVerificationError as exc:
        return exc.verify_code, exc.verify_message
    except Exception as exc:  # pragma: no cover - 網路異常
        return None, str(exc)


def normalize_dn_string(value: Optional[str]) -> str:
    if not value:
        return ""
    return (
        value.replace('"', '')
        .replace("'", "")
        .replace('`', '')
        .replace(', ', ',')
        .strip()
        .lower()
    )


def determine_self_signed(cert_result: CertificateResult) -> Optional[bool]:
    if cert_result.verify_code in {18, 19, 20, 21}:
        return True

    error_text = " ".join(
        filter(
            None,
            [
                (cert_result.error or ""),
                (cert_result.verify_error or ""),
            ],
        )
    ).lower()
    if "self signed" in error_text:
        return True

    issuer_norm = normalize_dn_string(cert_result.issuer)
    subject_norm = normalize_dn_string(cert_result.subject)

    if issuer_norm and subject_norm:
        return issuer_norm == subject_norm
    if issuer_norm or subject_norm:
        return False
    return None


def process_record(record: Dict[str, str], timeout: float) -> Dict[str, object]:
    host = sanitize_hostname(record.get("record_name") or record.get("domain_name"))
    if not host:
        result = dict(record)
        result.update(
            {
                "port_80_accessible": False,
                "port_80_response_time": None,
                "port_80_error": "record_name 缺失",
                "port_443_accessible": False,
                "port_443_response_time": None,
                "port_443_error": "record_name 缺失",
                "ssl_certificate": False,
                "cert_expiry_date": None,
                "days_until_expiry": None,
                "cert_issuer": None,
                "cert_subject": None,
                "cert_error": "record_name 缺失",
                "self_signed": None,
            }
        )
        return result

    connect_host = host
    record_type = (record.get("record_type") or "").strip().upper()
    record_value = sanitize_hostname(record.get("record_value"))
    if record_type == "A" and is_ip_address(record_value):
        connect_host = record_value

    port_80 = check_tcp_port(connect_host, 80, timeout)
    port_443, cert = check_https(connect_host, host, timeout)

    result = dict(record)
    result.update(
        {
            "port_80_accessible": port_80.accessible,
            "port_80_response_time": port_80.response_time,
            "port_80_error": port_80.error,
            "port_443_accessible": port_443.accessible,
            "port_443_response_time": port_443.response_time,
            "port_443_error": port_443.error,
            "ssl_certificate": cert.has_certificate,
            "cert_expiry_date": cert.expiry_iso,
            "days_until_expiry": cert.days_until_expiry,
            "cert_issuer": cert.issuer,
            "cert_subject": cert.subject,
            "cert_error": cert.error,
            "self_signed": cert.self_signed,
            "cert_trust_status": cert.trust_status,
            "cert_verify_code": cert.verify_code,
            "cert_verify_error": cert.verify_error,
        }
    )
    return result


def write_results(
    output_path: Path,
    records: List[Dict[str, object]],
    extra_fields: Iterable[str],
) -> None:
    if not records:
        print("沒有輸出資料，跳過寫檔。")
        return
    fieldnames = list(records[0].keys())
    for field in extra_fields:
        if field not in fieldnames:
            fieldnames.append(field)

    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in records:
            writer.writerow({key: format_value(row.get(key)) for key in fieldnames})


def format_value(value: object) -> object:
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return ""
    return value


def main() -> int:
    args = parse_args()

    input_path = Path(args.input_file).expanduser().resolve()
    if not input_path.exists():
        print(f"找不到輸入檔案: {input_path}", file=sys.stderr)
        return 1

    records = load_records(input_path)
    if not records:
        print("輸入檔案沒有任何資料。")
        return 0

    output_path = (
        Path(args.output_file).expanduser().resolve()
        if args.output_file
        else input_path.with_name(f"{input_path.stem}_accessibility{input_path.suffix}")
    )

    print(f"開始處理: {input_path}")
    print(f"讀取 {len(records)} 筆記錄，使用 {args.threads} 個執行緒，逾時 {args.timeout} 秒")

    processed: List[Dict[str, object]] = []
    extra_fields = [
        "port_80_accessible",
        "port_80_response_time",
        "port_80_error",
        "port_443_accessible",
        "port_443_response_time",
        "port_443_error",
        "ssl_certificate",
        "cert_expiry_date",
        "days_until_expiry",
        "cert_issuer",
        "cert_subject",
        "cert_error",
        "self_signed",
        "cert_trust_status",
        "cert_verify_code",
        "cert_verify_error",
    ]

    total = len(records)
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = executor.map(lambda r: process_record(r, args.timeout), records)
        for idx, result in enumerate(futures, start=1):
            processed.append(result)
            if idx % PROGRESS_INTERVAL == 0 or idx == total:
                print(f"已處理 {idx}/{total} 筆")

    write_results(output_path, processed, extra_fields)

    summary = summarize(processed)
    print("\n================= 統計 =================")
    for key, value in summary.items():
        print(f"{key}: {value}")
    print("======================================")
    print(f"結果已輸出到: {output_path}")

    return 0


def summarize(records: List[Dict[str, object]]) -> Dict[str, object]:
    total = len(records)
    port80_ok = sum(1 for r in records if r.get("port_80_accessible"))
    port443_ok = sum(1 for r in records if r.get("port_443_accessible"))
    cert_found = sum(1 for r in records if r.get("ssl_certificate"))
    return {
        "總記錄數": total,
        "80 端口可訪問": port80_ok,
        "443 端口可訪問": port443_ok,
        "取得 SSL 證書": cert_found,
    }


if __name__ == "__main__":
    sys.exit(main())
