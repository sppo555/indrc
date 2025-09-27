#!/usr/bin/env python3
import subprocess
import time
import re
import sys
import json
import random
from urllib import request, error as urlerror
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from datetime import datetime, timedelta
import argparse
import csv

# 顏色定義
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[0;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'  # No Color

# 配置參數
CONFIG = {
    'domains_file': 'domains.txt',
    'max_retries': 3,
    'retry_delay': 2,
    'whois_timeout': 5,
    'warning_days': 50,  # 預設50天內過期會警告
    'target_fields': [
        'Registrar:',
        'Registrar WHOIS Server:',
        'Updated Date:',
        'Creation Date:',
        'Registry Expiry Date:'
    ]
}

# 多個whois服務器
WHOIS_SERVERS = [
    None,  # 自動選擇
    # 將 .com/.net 的註冊局（Verisign）前置，減少受到快取/鏡像影響
    'whois.verisign-grs.com',
    'whois.nic.site',
    'whois.nic.club',
    'whois.nic.fun',
    'whois.nic.net',
    'whois.nic.online',
    'whois.nic.win',
    'whois.registrar-servers.com'
]

# 針對 .win 的常見註冊商 WHOIS（猜測清單，避免直接打 whois.nic.win 的限流）
REGISTRAR_GUESS_SERVERS_WIN = [
    'grs-whois.aliyun.com',
    'whois.godaddy.com',
    'whois.namecheap.com',
    'whois.tucows.com',
    'whois.publicdomainregistry.com'
]

class WhoisQueryResult:
    def __init__(self, success=False, data=None, server=None, elapsed_time=0, error=None):
        self.success = success
        self.data = data
        self.server = server
        self.elapsed_time = elapsed_time
        self.error = error

def run_whois_command(domain, server=None, timeout=5):
    """執行whois命令並返回結果"""
    try:
        if server:
            cmd = ['whois', '-h', server, domain]
            server_info = f"服務器: {server}"
        else:
            cmd = ['whois', domain]
            server_info = "自動選擇"
            
        start_time = time.time()
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=timeout,
            encoding='utf-8',
            errors='ignore'
        )
        elapsed_time = time.time() - start_time
        
        if result.returncode == 0:
            return WhoisQueryResult(
                success=True, 
                data=result.stdout, 
                server=server_info, 
                elapsed_time=elapsed_time
            )
        else:
            return WhoisQueryResult(
                success=False, 
                server=server_info, 
                elapsed_time=elapsed_time,
                error="command_failed"
            )
            
    except subprocess.TimeoutExpired:
        elapsed_time = timeout
        return WhoisQueryResult(
            success=False, 
            server=server_info, 
            elapsed_time=elapsed_time,
            error="timeout"
        )
    except Exception as e:
        return WhoisQueryResult(
            success=False, 
            server=server_info, 
            elapsed_time=0,
            error=str(e)
        )

def parse_expiry_date(whois_data):
    """從whois數據中解析過期日期"""
    if not whois_data:
        return None
    
    # 常見的過期日期格式
    date_patterns = [
        r'Registry Expiry Date:\s*([\d\-T:\s\.Z]+)',
        r'Expiry Date:\s*([\d\-T:\s\.Z]+)',
        r'Expiration Date:\s*([\d\-T:\s\.Z]+)',
        r'expires:\s*([\d\-T:\s\.Z]+)',
        r'expire:\s*([\d\-T:\s\.Z]+)',
        r'Expiration Time:\s*([\d\-T:\s\.Z]+)',
        r'Registry Expiry:\s*([\d\-T:\s\.Z]+)'
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, whois_data, re.IGNORECASE)
        if match:
            date_str = match.group(1).strip()
            # 嘗試解析不同的日期格式
            date_formats = [
                '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%dT%H:%M:%S.%fZ',
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%d',
                '%d-%m-%Y',
                '%d/%m/%Y',
                '%Y/%m/%d'
            ]
            
            for fmt in date_formats:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
    
    return None

def extract_target_fields(whois_data, target_fields):
    """從whois數據中提取目標欄位"""
    if not whois_data:
        return None
        
    lines = []
    for line in whois_data.split('\n'):
        line = line.strip()
        if any(field.lower() in line.lower() for field in target_fields):
            lines.append(line)
    
    return '\n'.join(lines) if lines else None

def parse_fields(whois_data):
    """解析常見的 WHOIS 欄位，回傳字典"""
    if not whois_data:
        return {}
    
    def find_value(label):
        # 建立忽略大小寫並允許冒號後空白的正則
        pattern = rf"^{re.escape(label)}\s*(.*)$"
        m = re.search(pattern, whois_data, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else ''
    
    return {
        'registrar': find_value('Registrar:'),
        'registrar_whois_server': find_value('Registrar WHOIS Server:'),
        'updated_date': find_value('Updated Date:'),
        'creation_date': find_value('Creation Date:'),
        'registry_expiry_date': find_value('Registry Expiry Date:'),
    }

def get_rdap_url(domain: str) -> str:
    """根據 TLD 產生 RDAP 查詢端點（目前覆蓋 .com/.net/.win）"""
    d = domain.strip().lower()
    if d.endswith('.com'):
        return f"https://rdap.verisign.com/com/v1/domain/{d}"
    if d.endswith('.net'):
        return f"https://rdap.verisign.com/net/v1/domain/{d}"
    if d.endswith('.win'):
        return f"https://rdap.nic.win/domain/{d}"
    return ''

def rdap_fetch(domain: str, timeout: int = 8):
    """發送 RDAP 請求並回傳 JSON 物件，失敗回傳 None"""
    url = get_rdap_url(domain)
    if not url:
        return None
    try:
        req = request.Request(url, headers={
            'Accept': 'application/rdap+json, application/json;q=0.9,*/*;q=0.8',
            'User-Agent': 'whois-batch/1.0'
        })
        with request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode('utf-8', 'ignore')
            return json.loads(data)
    except (urlerror.URLError, urlerror.HTTPError, TimeoutError, ValueError):
        return None

def rdap_to_whois_text(rdap_json: dict) -> str:
    """將 RDAP JSON 轉為類似 WHOIS 的文字，方便沿用既有解析邏輯"""
    if not rdap_json:
        return ''
    lines = []
    # Registrar 名稱
    try:
        for ent in rdap_json.get('entities', []):
            if 'registrar' in ent.get('roles', []):
                # vcardArray 可能包含名稱
                v = ent.get('vcardArray', [])
                if len(v) == 2 and isinstance(v[1], list):
                    for item in v[1]:
                        if isinstance(item, list) and len(item) >= 4 and item[0] == 'fn':
                            lines.append(f"Registrar: {item[3]}")
                            break
                break
    except Exception:
        pass
    # expiration 事件
    try:
        for ev in rdap_json.get('events', []):
            if ev.get('eventAction') == 'expiration' and ev.get('eventDate'):
                lines.append(f"Registry Expiry Date: {ev.get('eventDate')}")
                break
    except Exception:
        pass
    return "\n".join(lines)

def query_domain_single_attempt(domain, attempt, max_attempts):
    """單次嘗試查詢域名（以註冊局為優先，取消過早返回）"""
    print(f"{Colors.BLUE}正在查詢: {domain} (嘗試 {attempt}/{max_attempts}){Colors.NC}")

    registry_host = 'whois.verisign-grs.com'
    best_success = None  # 儲存第一個成功（作為回退）
    registry_success = None  # 儲存註冊局的成功結果（優先採用）

    # 針對 .win：改為「只用 WHOIS」，且優先嘗試常見註冊商，避免直接打 whois.nic.win 造成限流
    is_win = domain.lower().endswith('.win')
    local_timeout = 10 if is_win else CONFIG['whois_timeout']
    if is_win:
        print(f"{Colors.YELLOW}  ⚬ .win 採用『註冊商 WHOIS 優先，避免 nic.win』策略{Colors.NC}")
        # 先嘗試猜測的註冊商 WHOIS 列表
        for guess in REGISTRAR_GUESS_SERVERS_WIN:
            print(f"{Colors.YELLOW}    ▷ 嘗試註冊商 WHOIS: {guess} ({local_timeout}s 超時){Colors.NC}")
            time.sleep(0.2 + random.uniform(0, 0.4))
            res = run_whois_command(domain, guess, local_timeout)
            if not res.error and parse_expiry_date(res.data):
                print(f"{Colors.GREEN}    ✓ 成功（註冊商 WHOIS 命中）{Colors.NC}")
                return True, res.data
        # 若猜測未命中，再進入一般迴圈（但我們稍後會跳過 whois.nic.win）

    for server in WHOIS_SERVERS:
        server_info = "自動選擇" if server is None else f"服務器: {server}"
        print(f"{Colors.YELLOW}  ⚬ 嘗試 {server_info} ({local_timeout}s 超時)...{Colors.NC}")

        # 對 .win 在每次查詢前加隨機抖動，降低觸發限流機率
        if is_win:
            time.sleep(0.3 + random.uniform(0, 0.6))

        # 若為 .win，跳過 whois.nic.win 以避免立即遭遇限流
        if is_win and server == 'whois.nic.win':
            print(f"{Colors.YELLOW}    ⚬ 跳過 whois.nic.win 以避免限流{Colors.NC}")
            continue

        # 執行whois查詢
        result = run_whois_command(domain, server, local_timeout)

        # 處理不同的錯誤情況
        if result.error == "timeout":
            print(f"{Colors.RED}    ✗ 超時 ({result.elapsed_time:.1f}s){Colors.NC}")
            continue
        elif result.error:
            print(f"{Colors.RED}    ✗ 連接失敗 ({result.elapsed_time:.1f}s) - {result.error}{Colors.NC}")
            continue

        lower_data = (result.data or '').lower()
        # 偵測限流字串（此處不再使用 RDAP 回退，維持純 WHOIS 策略）
        if 'number of allowed queries exceeded' in lower_data or 'limit exceeded' in lower_data:
            print(f"{Colors.YELLOW}    ⚬ 偵測到註冊局限流，啟動退避並嘗試 RDAP{Colors.NC}")
            # 退避等待（帶抖動）
            sleep_s = min(10, 2 * attempt) + random.uniform(0.2, 0.8)
            time.sleep(sleep_s)
            # 若 RDAP 仍失敗，繼續嘗試下一個 whois 來源
            continue

        # 檢查是否有目標欄位資料（僅作為顯示用途）
        target_data = extract_target_fields(result.data, CONFIG['target_fields'])
        # 放寬成功判定：只要能解析到期日就視為成功
        parsed_expiry = parse_expiry_date(result.data)

        if target_data or parsed_expiry:
            print(f"{Colors.GREEN}    ✓ 成功 ({result.elapsed_time:.1f}s){Colors.NC}")
            print(f"{Colors.GREEN}✓ 成功: {domain} ({result.server}){Colors.NC}")
            if target_data:
                print(target_data)

            # 解析過期日期（僅輸出訊息，不在此處返回，改為走優先級決策）
            expiry_date = parsed_expiry
            if expiry_date:
                days_until_expiry = (expiry_date - datetime.now()).days
                if days_until_expiry < 0:
                    print(f"{Colors.RED}⚠️  域名已過期 {abs(days_until_expiry)} 天！{Colors.NC}")
                elif days_until_expiry <= CONFIG['warning_days']:
                    print(f"{Colors.YELLOW}⚠️  域名將在 {days_until_expiry} 天後過期！{Colors.NC}")
                else:
                    print(f"{Colors.GREEN}✓ 域名還有 {days_until_expiry} 天到期{Colors.NC}")

            print("----------------------------------------")

            # 優先：若是註冊局回應，直接採用
            if server == registry_host:
                registry_success = result.data
                break

            # 否則先記錄為回退候選
            if best_success is None:
                best_success = result.data
        else:
            # 檢查域名是否存在
            if re.search(r'no match|not found|no data found|domain.*not.*exist', result.data, re.IGNORECASE):
                print(f"{Colors.YELLOW}    ⚬ 域名不存在或無註冊資料 ({result.elapsed_time:.1f}s){Colors.NC}")
            else:
                print(f"{Colors.YELLOW}    ⚬ 無相關資料 ({result.elapsed_time:.1f}s){Colors.NC}")

            # 若回包中有 Registrar WHOIS Server，可嘗試切換到該註冊商來源
            fields = parse_fields(result.data)
            registrar_whois = fields.get('registrar_whois_server', '')
            if registrar_whois:
                print(f"{Colors.YELLOW}    ⚬ 嘗試註冊商 WHOIS: {registrar_whois}{Colors.NC}")
                # 註冊商查詢也加上 .win 的抖動與較長超時
                if is_win:
                    time.sleep(0.3 + random.uniform(0, 0.6))
                reg_res = run_whois_command(domain, registrar_whois, local_timeout)
                if not reg_res.error:
                    # 再次判定是否成功
                    if parse_expiry_date(reg_res.data):
                        print(f"{Colors.GREEN}    ✓ 註冊商 WHOIS 成功{Colors.NC}")
                        return True, reg_res.data
                else:
                    print(f"{Colors.RED}    ✗ 註冊商 WHOIS 失敗 - {reg_res.error}{Colors.NC}")

    # 最終決策：註冊局優先，其次使用第一個成功結果
    if registry_success is not None:
        return True, registry_success
    if best_success is not None:
        return True, best_success

    return False, None

def query_domain_with_retry(domain):
    """帶重試機制的域名查詢"""
    for attempt in range(1, CONFIG['max_retries'] + 1):
        success, whois_data = query_domain_single_attempt(domain, attempt, CONFIG['max_retries'])
        if success:
            return True, whois_data
        
        if attempt < CONFIG['max_retries']:
            print(f"{Colors.YELLOW}⚠ 重試: {domain} (第 {attempt} 次失敗，等待 {CONFIG['retry_delay']}s 後重試){Colors.NC}")
            time.sleep(CONFIG['retry_delay'])
        else:
            print(f"{Colors.RED}✗ 失敗: {domain} (已達最大重試次數){Colors.NC}")
            print("----------------------------------------")
    
    return False, None

def main():
    # 解析命令行參數
    parser = argparse.ArgumentParser(description='批量查詢域名whois資訊並檢查過期狀態')
    parser.add_argument('-d', '--days', type=int, default=CONFIG['warning_days'], 
                       help=f'設定警告天數，域名在此天數內過期會顯示警告 (預設: {CONFIG["warning_days"]}天)')
    parser.add_argument('-f', '--file', default='domains.txt',
                       help='域名列表文件 (預設: domains.txt)')
    parser.add_argument('-o', '--output', default=f"whois_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                       help='輸出 CSV 檔名 (預設: whois_results_YYYYMMDD_HHMMSS.csv)')
    args = parser.parse_args()
    
    # 更新配置
    CONFIG['warning_days'] = args.days
    CONFIG['domains_file'] = args.file
    
    # 檢查域名檔案
    domains_file = Path(CONFIG['domains_file'])
    if not domains_file.exists():
        print(f"{Colors.RED}錯誤: 找不到檔案 {CONFIG['domains_file']}{Colors.NC}")
        sys.exit(1)
    
    # 檢查whois命令
    try:
        subprocess.run(['whois', '--version'], capture_output=True, timeout=3)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print(f"{Colors.RED}錯誤: 系統中未安裝 whois 命令{Colors.NC}")
        print("請安裝 whois: sudo apt install whois (Ubuntu/Debian) 或 brew install whois (macOS)")
        sys.exit(1)
    
    # 讀取域名列表
    domains = []
    try:
        with open(domains_file, 'r', encoding='utf-8') as f:
            for line in f:
                domain = line.strip()
                if domain:  # 跳過空行
                    domains.append(domain)
    except Exception as e:
        print(f"{Colors.RED}錯誤: 無法讀取域名檔案 - {e}{Colors.NC}")
        sys.exit(1)
    
    if not domains:
        print(f"{Colors.RED}錯誤: 域名檔案為空{Colors.NC}")
        sys.exit(1)
    
    # 開始處理
    print(f"{Colors.BLUE}開始批量查詢域名whois資訊...{Colors.NC}")
    print(f"{Colors.GREEN}✓ 使用 {CONFIG['whois_timeout']}s 超時限制{Colors.NC}")
    print(f"✓ 共 {len(domains)} 個域名待查詢")
    print("==========================================")
    
    # 統計變數
    success_count = 0
    failed_count = 0
    start_time = time.time()
    expired_domains = []
    warning_domains = []
    safe_domains = []
    csv_rows = []
    
    # 處理每個域名
    for i, domain in enumerate(domains, 1):
        print(f"\n[{i}/{len(domains)}] 處理域名: {domain}")
        success, whois_data = query_domain_with_retry(domain)
        if success:
            success_count += 1
            # 分析過期狀態
            expiry_date = parse_expiry_date(whois_data)
            if expiry_date:
                days_until_expiry = (expiry_date - datetime.now()).days
                expiry_str = expiry_date.strftime('%Y-%m-%d')
                
                if days_until_expiry < 0:
                    expired_domains.append({
                        'domain': domain,
                        'days': abs(days_until_expiry),
                        'expiry_date': expiry_str
                    })
                    status = 'expired'
                elif days_until_expiry <= CONFIG['warning_days']:
                    warning_domains.append({
                        'domain': domain,
                        'days': days_until_expiry,
                        'expiry_date': expiry_str
                    })
                    status = 'warning'
                else:
                    safe_domains.append({
                        'domain': domain,
                        'days': days_until_expiry,
                        'expiry_date': expiry_str
                    })
                    status = 'safe'
                
                # 產生 CSV 列
                fields = parse_fields(whois_data)
                csv_rows.append({
                    'domain': domain,
                    'status': status,
                    'expiry_date': expiry_str,
                    'days_until_expiry': days_until_expiry,
                    'registrar': fields.get('registrar', ''),
                    'registrar_whois_server': fields.get('registrar_whois_server', ''),
                    'updated_date': fields.get('updated_date', ''),
                    'creation_date': fields.get('creation_date', ''),
                    'registry_expiry_date_raw': fields.get('registry_expiry_date', ''),
                })
            else:
                # 沒有解析到過期日期，仍寫入基本資料
                fields = parse_fields(whois_data)
                csv_rows.append({
                    'domain': domain,
                    'status': 'unknown',
                    'expiry_date': '',
                    'days_until_expiry': '',
                    'registrar': fields.get('registrar', ''),
                    'registrar_whois_server': fields.get('registrar_whois_server', ''),
                    'updated_date': fields.get('updated_date', ''),
                    'creation_date': fields.get('creation_date', ''),
                    'registry_expiry_date_raw': fields.get('registry_expiry_date', ''),
                })
        else:
            failed_count += 1
            # 失敗也寫入 CSV
            csv_rows.append({
                'domain': domain,
                'status': 'failed',
                'expiry_date': '',
                'days_until_expiry': '',
                'registrar': '',
                'registrar_whois_server': '',
                'updated_date': '',
                'creation_date': '',
                'registry_expiry_date_raw': '',
            })
    
    # 輸出 CSV
    csv_headers = [
        'domain', 'status', 'expiry_date', 'days_until_expiry',
        'registrar', 'registrar_whois_server', 'updated_date', 'creation_date',
        'registry_expiry_date_raw'
    ]
    try:
        with open(args.output, 'w', encoding='utf-8-sig', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=csv_headers)
            writer.writeheader()
            for row in csv_rows:
                writer.writerow(row)
        print(f"\n{Colors.GREEN}✓ CSV 已輸出: {args.output}{Colors.NC}")
    except Exception as e:
        print(f"\n{Colors.RED}錯誤: 寫入 CSV 失敗 - {e}{Colors.NC}")
    
    # 顯示統計結果
    total_time = time.time() - start_time
    total_domains = len(domains)
    success_rate = (success_count * 100 / total_domains) if total_domains > 0 else 0
    
    print("\n==========================================")
    print(f"{Colors.BLUE}查詢完成統計:{Colors.NC}")
    print(f"總共域名: {total_domains}")
    print(f"{Colors.GREEN}成功: {success_count}{Colors.NC}")
    print(f"{Colors.RED}失敗: {failed_count}{Colors.NC}")
    print(f"成功率: {success_rate:.1f}%")
    print(f"總耗時: {total_time:.1f}s")
    
    # 顯示過期狀態摘要
    expired_count = len(expired_domains)
    warning_count = len(warning_domains)
    safe_count = len(safe_domains)
    
    if expired_domains or warning_domains:
        print(f"\n{Colors.CYAN}===========================================")
        print(f"過期狀態摘要 (警告天數: {CONFIG['warning_days']}天):{Colors.NC}")
        print(f"{Colors.RED}已過期域名: {expired_count}{Colors.NC}")
        print(f"{Colors.YELLOW}即將過期域名 ({CONFIG['warning_days']}天內): {warning_count}{Colors.NC}")
        print(f"{Colors.GREEN}安全域名: {safe_count}{Colors.NC}")
        
        if expired_domains:
            print(f"\n{Colors.RED}🚨 已過期域名列表:{Colors.NC}")
            for item in sorted(expired_domains, key=lambda x: x['days'], reverse=True):
                print(f"  • {item['domain']} - 已過期 {item['days']} 天 (過期日期: {item['expiry_date']})")
        
        if warning_domains:
            print(f"\n{Colors.YELLOW}⚠️  即將過期域名列表:{Colors.NC}")
            for item in sorted(warning_domains, key=lambda x: x['days']):
                print(f"  • {item['domain']} - {item['days']} 天後過期 (過期日期: {item['expiry_date']})")
        
        print(f"{Colors.CYAN}==========================================={Colors.NC}")
    else:
        print(f"\n{Colors.GREEN}✓ 所有查詢成功的域名都在安全期限內！{Colors.NC}")
    
    # 最後輸出即將過期域名的簡潔列表
    if warning_domains:
        print(f"\n{Colors.YELLOW}===========================================")
        print(f"⚠️  即將過期域名清單 ({CONFIG['warning_days']}天內):{Colors.NC}")
        warning_domain_names = [item['domain'] for item in sorted(warning_domains, key=lambda x: x['days'])]
        for domain in warning_domain_names:
            print(f"{Colors.YELLOW}{domain}{Colors.NC}")
        print(f"{Colors.YELLOW}==========================================={Colors.NC}")
        
        # 也輸出純文字版本方便複製
        print(f"\n{Colors.CYAN}純文字版本 (方便複製):{Colors.NC}")
        print('\n'.join(warning_domain_names))

if __name__ == "__main__":
    main()