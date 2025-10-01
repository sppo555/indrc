#!/usr/bin/env python3
"""列出阿里雲 DNS 所有域名的 A 與 CNAME 記錄。

使用 AccessKey 認證，遍歷全部域名並輸出符合類型的記錄至 CSV。
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import hmac
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests import Response

API_ENDPOINT = "https://alidns.aliyuncs.com/"
API_VERSION = "2015-01-09"
TAG_API_ENDPOINT = "https://tag.aliyuncs.com/"
TAG_API_VERSION = "2018-08-28"
DEFAULT_TIMEOUT = 10
DEFAULT_PAGE_SIZE = 100
DEFAULT_TYPES = {"A", "CNAME"}
DEFAULT_TAG_RESOURCE_TYPES = ["DOMAIN", "ALIDNS", "ALIDNS_DOMAIN"]
DEFAULT_TAG_REGIONS = ["cn-hangzhou"]
DEFAULT_TAG_BATCH_SIZE = 20


class AliyunDNSClient:
    """簡易的阿里雲 DNS API 客戶端，使用 HMAC-SHA1 簽名。"""

    def __init__(self, access_key_id: str, access_key_secret: str, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.timeout = timeout

    def list_domains(self) -> List[Dict[str, str]]:
        """取得全部域名列表。"""
        page_number = 1
        domains: List[Dict[str, str]] = []
        while True:
            payload = {
                "PageNumber": page_number,
                "PageSize": DEFAULT_PAGE_SIZE,
                "NeedDetailAttributes": True,
            }
            data = self._request("DescribeDomains", payload)
            total_count = int(data.get("TotalCount", 0))
            domain_items = data.get("Domains", {}).get("Domain", [])
            domains.extend(domain_items)
            if page_number * DEFAULT_PAGE_SIZE >= total_count:
                break
            page_number += 1
            time.sleep(0.1)
        return domains

    def get_domain(self, domain_name: str) -> Dict[str, str]:
        """以 EXACT + KeyWord 精準取得單一域名（含詳細屬性與 Tags）。"""
        payload = {
            "SearchMode": "EXACT",
            "KeyWord": domain_name,
            "NeedDetailAttributes": True,
            "PageNumber": 1,
            "PageSize": 20,
        }
        data = self._request("DescribeDomains", payload)
        items = data.get("Domains", {}).get("Domain", [])
        for item in items:
            if str(item.get("DomainName", "")).lower() == domain_name.lower():
                return item
        return {}

    def list_domain_records(self, domain_name: str) -> Iterable[Dict[str, str]]:
        """列出單一域名全部 DNS 記錄。"""
        page_number = 1
        while True:
            payload = {
                "DomainName": domain_name,
                "PageNumber": page_number,
                "PageSize": DEFAULT_PAGE_SIZE,
            }
            data = self._request("DescribeDomainRecords", payload)
            records = data.get("DomainRecords", {}).get("Record", [])
            for record in records:
                yield record
            total_count = int(data.get("TotalCount", 0))
            if page_number * DEFAULT_PAGE_SIZE >= total_count:
                break
            page_number += 1
            time.sleep(0.1)

    def _request(self, action: str, params: Dict[str, object]) -> Dict[str, object]:
        request_params = self._build_common_params(action)
        request_params.update(params)
        request_params["Signature"] = self._sign(request_params)
        response = requests.get(API_ENDPOINT, params=request_params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if "Code" in payload and payload.get("Code") != "OK":
            message = payload.get("Message", "未知錯誤")
            raise RuntimeError(f"Aliyun API 錯誤: {payload.get('Code')}: {message}")
        return payload

    def _build_common_params(self, action: str) -> Dict[str, object]:
        timestamp = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "Action": action,
            "Version": API_VERSION,
            "Format": "JSON",
            "AccessKeyId": self.access_key_id,
            "Timestamp": timestamp,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
        }

    def _sign(self, params: Dict[str, object]) -> str:
        canonicalized = "&".join(
            f"{_percent_encode(k)}={_percent_encode(params[k])}" for k in sorted(params)
        )
        string_to_sign = f"GET&%2F&{_percent_encode(canonicalized)}"
        signing_key = f"{self.access_key_secret}&"
        signature = hmac.new(
            signing_key.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(signature).decode("utf-8")


def _percent_encode(value: object) -> str:
    from urllib.parse import quote

    encoded = quote(str(value), safe="~")
    return encoded.replace("+", "%20").replace("*", "%2A").replace("%7E", "~")


class AliyunTagClient:
    """阿里雲通用標籤服務客戶端，使用 HMAC-SHA1 簽名呼叫 ListTagResources。"""

    def __init__(self, access_key_id: str, access_key_secret: str, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.timeout = timeout

    def list_tags_for_resources(self, resource_type: str, resource_ids: List[str], region_id: str = "cn-hangzhou", *, batch_size: int = DEFAULT_TAG_BATCH_SIZE) -> Dict[str, List[Dict[str, str]]]:
        """查詢多個資源的標籤，回傳 {resource_id: [{Key, Value}, ...]} 映射。

        注意：部分服務對 resource_id 的定義不同，若查不到可嘗試以其他 ID（如 DomainId / DomainName）。
        """
        if not resource_ids:
            return {}
        # 阿里雲 ListTagResources 支援最多 50 筆 ResourceId.*，預設一批 20 可調整
        result: Dict[str, List[Dict[str, str]]] = {}
        for i in range(0, len(resource_ids), batch_size):
            batch = resource_ids[i : i + batch_size]
            params: Dict[str, object] = {
                "Action": "ListTagResources",
                "Version": TAG_API_VERSION,
                "Format": "JSON",
                "AccessKeyId": self.access_key_id,
                "Timestamp": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "SignatureMethod": "HMAC-SHA1",
                "SignatureVersion": "1.0",
                "SignatureNonce": str(uuid.uuid4()),
                "RegionId": region_id,
                "ResourceType": resource_type,
            }
            # ResourceId.N 從 1 開始
            for idx, rid in enumerate(batch, start=1):
                params[f"ResourceId.{idx}"] = rid

            signature = _sign_params(params, self.access_key_secret)
            params["Signature"] = signature
            resp = requests.get(TAG_API_ENDPOINT, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            # 結構範例：{"TagResources": {"TagResource": [{"ResourceId": "xxx", "TagKey": "ns", "TagValue": "cf"}, ...]}}
            items = (
                data.get("TagResources", {}).get("TagResource", [])
                if isinstance(data, dict)
                else []
            )
            for item in items:
                rid = str(item.get("ResourceId", ""))
                if not rid:
                    continue
                result.setdefault(rid, []).append({
                    "Key": str(item.get("TagKey", "")),
                    "Value": str(item.get("TagValue", "")),
                })
            time.sleep(0.1)
        return result


def _sign_params(params: Dict[str, object], access_key_secret: str) -> str:
    canonicalized = "&".join(
        f"{_percent_encode(k)}={_percent_encode(params[k])}" for k in sorted(params)
    )
    string_to_sign = f"GET&%2F&{_percent_encode(canonicalized)}"
    signing_key = f"{access_key_secret}&"
    signature = hmac.new(
        signing_key.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(signature).decode("utf-8")


def load_access_key_from_csv(file_path: Path) -> Tuple[str, str]:
    with file_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        row = next(reader, None)
        if not row or "AccessKey ID" not in row or "AccessKey Secret" not in row:
            raise ValueError("AccessKey CSV 檔案格式錯誤，需包含 'AccessKey ID' 與 'AccessKey Secret' 欄位。")
        return row["AccessKey ID"].strip(), row["AccessKey Secret"].strip()


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="列出阿里雲 DNS 中所有域名的 A/CNAME 記錄")
    parser.add_argument(
        "--access-key-file",
        default="/Users/alex/Downloads/AccessKey.csv",
        help="AccessKey CSV 檔案路徑，預設為 /Users/alex/Downloads/AccessKey.csv",
    )
    parser.add_argument(
        "--types",
        default=",".join(sorted(DEFAULT_TYPES)),
        help="需要輸出的 DNS 類型，使用逗號分隔，例如 A,CNAME",
    )
    parser.add_argument(
        "--output",
        default=f"aliyun_dns_records_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv",
        help="輸出 CSV 檔案路徑，預設為當前目錄自動命名",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="在 API 分頁請求間隔的秒數，避免觸發流量限制",
    )
    parser.add_argument(
        "--only-domain",
        help="只處理指定主域名（精準匹配）。例如 --only-domain pokerclubin.fun",
    )
    parser.add_argument(
        "--include-tags",
        action="store_true",
        help="是否包含資源標籤（會呼叫 Tag 服務 ListTagResources）",
    )
    parser.add_argument(
        "--tag-region",
        default="cn-hangzhou",
        help="Tag 服務查詢 RegionId，預設 cn-hangzhou",
    )
    parser.add_argument(
        "--tag-regions",
        default=",".join(DEFAULT_TAG_REGIONS),
        help="以逗號分隔的多個 RegionId，會逐一嘗試（預設只使用 cn-hangzhou）",
    )
    parser.add_argument(
        "--tag-resource-types",
        default=",".join(DEFAULT_TAG_RESOURCE_TYPES),
        help="以逗號分隔的 ResourceType 值，會逐一嘗試（預設: DOMAIN,ALIDNS,ALIDNS_DOMAIN）",
    )
    parser.add_argument(
        "--tag-batch-size",
        type=int,
        default=DEFAULT_TAG_BATCH_SIZE,
        help="ListTagResources 單批查詢數量（最大 50，預設 20）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="並行抓取域名解析記錄的工作執行緒數（預設 8）",
    )
    return parser.parse_args(argv)


def write_records_to_csv(records: List[Dict[str, object]], output_path: Path) -> None:
    fieldnames = [
        "DomainName",
        "DomainId",
        "DomainGroupName",
        "GroupId",
        "ResourceGroupId",
        "DomainTags",
        "RecordId",
        "RR",
        "Type",
        "Value",
        "TTL",
        "Priority",
        "Line",
        "Status",
        "Locked",
        "Remark",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.get(key, "") for key in fieldnames})


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    access_key_path = Path(args.access_key_file).expanduser()
    if not access_key_path.exists():
        print(f"找不到 AccessKey 檔案: {access_key_path}", file=sys.stderr)
        return 1

    try:
        access_key_id, access_key_secret = load_access_key_from_csv(access_key_path)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"讀取 AccessKey 失敗: {exc}", file=sys.stderr)
        return 1

    desired_types = {item.strip().upper() for item in args.types.split(",") if item.strip()}
    client = AliyunDNSClient(access_key_id, access_key_secret)

    try:
        if args.only_domain:
            d = client.get_domain(args.only_domain.strip())
            domains = [d] if d else []
        else:
            domains = client.list_domains()
    except Exception as exc:  # pylint: disable=broad-except
        print(f"取得域名列表失敗: {exc}", file=sys.stderr)
        return 1

    print(f"共取得 {len(domains)} 個域名，開始抓取 DNS 記錄...")
    # 若需要標籤：
    # 1) 先從 DescribeDomains 回傳的 domain["Tags"]["Tag"] 直接取用（最準確）
    # 2) 若無資料，再呼叫通用 Tag 服務補齊
    domain_id_list = [d.get("DomainId", "") for d in domains if d.get("DomainId")]
    domain_name_list = [d.get("DomainName", "") for d in domains if d.get("DomainName")]
    tags_by_domain: Dict[str, List[Dict[str, str]]] = {}
    if args.include_tags:
        # 先從域名物件直接萃取（兼容兩種結構：dict 包含 Tag 陣列；或直接為 list）
        for d in domains:
            raw_tags: List[Dict[str, str]] = []
            tags_container = d.get("Tags")
            if isinstance(tags_container, dict):
                raw_tags = tags_container.get("Tag", []) or []
            elif isinstance(tags_container, list):
                raw_tags = tags_container

            # 正規化為 {Key, Value}
            norm_tags: List[Dict[str, str]] = []
            for t in raw_tags:
                if not isinstance(t, dict):
                    continue
                key = str(t.get("Key") or t.get("TagKey") or "")
                val = str(t.get("Value") or t.get("TagValue") or "")
                if key or val:
                    norm_tags.append({"Key": key, "Value": val})
            if norm_tags:
                if d.get("DomainId"):
                    tags_by_domain[d["DomainId"]] = norm_tags
                if d.get("DomainName"):
                    tags_by_domain[d["DomainName"]] = norm_tags

        # 若仍需要補齊則呼叫 Tag 服務
        try:
            missing_ids = [rid for rid in domain_id_list if rid and rid not in tags_by_domain]
            missing_names = [rn for rn in domain_name_list if rn and rn not in tags_by_domain]
            if missing_ids or missing_names:
                tag_client = AliyunTagClient(access_key_id, access_key_secret)
                regions = [r.strip() for r in (args.tag_regions or "").split(",") if r.strip()] or [args.tag_region]
                rtypes = [t.strip() for t in (args.tag_resource_types or "").split(",") if t.strip()] or DEFAULT_TAG_RESOURCE_TYPES
                # 逐 Region × ResourceType 嘗試，直到補齊或嘗試完
                for region in regions:
                    for rtype in rtypes:
                        left_ids = [rid for rid in missing_ids if rid not in tags_by_domain]
                        left_names = [rn for rn in missing_names if rn not in tags_by_domain]
                        if not left_ids and not left_names:
                            break
                        if left_ids:
                            tags_by_id = tag_client.list_tags_for_resources(
                                resource_type=rtype, resource_ids=left_ids, region_id=region, batch_size=args.tag_batch_size
                            )
                            # 只合併有資料的鍵
                            for k, v in tags_by_id.items():
                                if v:
                                    tags_by_domain[k] = v
                        if left_names:
                            tags_by_name = tag_client.list_tags_for_resources(
                                resource_type=rtype, resource_ids=left_names, region_id=region, batch_size=args.tag_batch_size
                            )
                            for k, v in tags_by_name.items():
                                if v:
                                    tags_by_domain[k] = v
        except Exception as exc:  # pylint: disable=broad-except
            print(f"查詢標籤失敗（將不輸出 DomainTags）: {exc}", file=sys.stderr)
    # 多執行緒以域名為單位抓取解析紀錄
    def worker(idx: int, domain: Dict[str, object]) -> List[Dict[str, object]]:
        domain_name = str(domain.get("DomainName", ""))
        print(f"[{idx}/{len(domains)}] 處理 {domain_name} ...")
        out: List[Dict[str, object]] = []
        try:
            for rec in client.list_domain_records(domain_name):
                if desired_types and str(rec.get("Type", "")).upper() not in desired_types:
                    continue
                r = dict(rec)
                r.setdefault("DomainName", domain_name)
                r.setdefault("DomainId", domain.get("DomainId", ""))
                r.setdefault("DomainGroupName", domain.get("GroupName", ""))
                r.setdefault("GroupId", domain.get("GroupId", ""))
                r.setdefault("ResourceGroupId", domain.get("ResourceGroupId", ""))
                # 標籤
                domain_tags: List[Dict[str, str]] = []
                if args.include_tags:
                    domain_tags = tags_by_domain.get(domain.get("DomainId", ""), []) or tags_by_domain.get(domain_name, [])
                    # 若未命中，嘗試即時回補：DescribeDomains(EXACT)
                    if not domain_tags:
                        try:
                            got = client.get_domain(domain_name)
                            if got:
                                raw_tags: List[Dict[str, str]] = []
                                tags_container = got.get("Tags")
                                if isinstance(tags_container, dict):
                                    raw_tags = tags_container.get("Tag", []) or []
                                elif isinstance(tags_container, list):
                                    raw_tags = tags_container or []
                                norm: List[Dict[str, str]] = []
                                for t in raw_tags:
                                    if not isinstance(t, dict):
                                        continue
                                    k = str(t.get("Key") or t.get("TagKey") or "")
                                    v = str(t.get("Value") or t.get("TagValue") or "")
                                    if k or v:
                                        norm.append({"Key": k, "Value": v})
                                if norm:
                                    tags_by_domain[got.get("DomainId", domain_name) or domain_name] = norm
                                    tags_by_domain[domain_name] = norm
                                    domain_tags = norm
                        except Exception:
                            pass
                if domain_tags:
                    r.setdefault(
                        "DomainTags",
                        ";".join(
                            [f"{t.get('Key','')}={t.get('Value','')}".strip("=") for t in domain_tags if (t.get("Key") or t.get("Value"))]
                        ),
                    )
                else:
                    r.setdefault("DomainTags", "")
                r.setdefault("RecordId", r.get("RecordId", ""))
                r.setdefault("Remark", r.get("Remark", ""))
                out.append(r)
                if args.sleep:
                    time.sleep(args.sleep)
        except Exception as e:  # pylint: disable=broad-except
            print(f"域名 {domain_name} 取得記錄失敗: {e}", file=sys.stderr)
        return out

    records: List[Dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures = {executor.submit(worker, i, d): i for i, d in enumerate(domains, start=1)}
        for fut in as_completed(futures):
            try:
                records.extend(fut.result() or [])
            except Exception as e:  # 安全保護
                print(f"執行緒錯誤: {e}", file=sys.stderr)

    if not records:
        print("未找到符合條件的 DNS 記錄。")
        return 0

    output_path = Path(args.output)
    write_records_to_csv(records, output_path)
    print(f"已輸出 {len(records)} 筆 DNS 記錄至 {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
