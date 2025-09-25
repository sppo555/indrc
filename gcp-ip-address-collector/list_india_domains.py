import os
import json
import csv
import argparse
from datetime import datetime
from typing import List, Dict

import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build
import subprocess
from google.oauth2.credentials import Credentials as UserCredentials
def resolve_credentials_path(cli_path: str | None = None) -> str | None:
    """Resolves the credentials path similarly to list_ips.py."""
    if cli_path and os.path.isfile(cli_path):
        return cli_path

    env_override = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("GCP_SA_KEY_PATH")
    if env_override and os.path.isfile(env_override):
        print(f"找到凭据在环境变量中: {env_override}")
        return env_override

    base_dir = os.path.dirname(__file__)
    candidates = [
        os.path.join(base_dir, 'incdr-infra.json'),
        os.path.join(base_dir, '..', 'incdr-infra.json'),
        os.path.expanduser('~/Documents/incdr-infra.json'),
        os.path.expanduser('~/incdr-infra.json'),
    ]
    for p in candidates:
        if os.path.isfile(p):
            print(f"在常见路径找到凭据: {p}")
            return p

    return None


def list_cloud_domains(project_id: str, credentials) -> List[Dict]:
    print(f"正在获取项目 {project_id} 的 Cloud Domains...")
    service = build('domains', 'v1', credentials=credentials)

    parent = f"projects/{project_id}/locations/-"
    request = service.projects().locations().registrations().list(parent=parent)

    results: List[Dict] = []
    while request is not None:
        response = request.execute()
        for reg in response.get('registrations', []):
            name_full = reg.get('name', '')  # projects/{p}/locations/{loc}/registrations/{id}
            domain = reg.get('domainName') or reg.get('labels', {}).get('domain')
            state = reg.get('state')
            expire_time = reg.get('expireTime') or reg.get('expireTimeTime')
            contact_privacy = reg.get('contactSettings', {}).get('privacy')
            dns_settings = reg.get('dnsSettings', {})
            mgmt = reg.get('managementSettings', {})

            # 解析 DNS 设置获取详细信息
            dns_info = parse_dns_settings(dns_settings)

            results.append({
                'project_id': project_id,
                'registration_name': name_full,
                'domain_name': domain,
                'state': state,
                'expire_time': expire_time,
                'contact_privacy': contact_privacy,
                'dns_settings': json.dumps(dns_settings),
                'management_settings': json.dumps(mgmt),
                'dns_provider': dns_info.get('provider', '未知'),
                'custom_dns_records': json.dumps(dns_info.get('custom_records', [])),
                'google_domains_dns': dns_info.get('google_domains_dns', False),
                'name_servers': json.dumps(dns_info.get('name_servers', [])),
            })
        request = service.projects().locations().registrations().list_next(
            previous_request=request, previous_response=response
        )

    print(f"在项目 {project_id} 中找到 {len(results)} 个域名注册。")
    return results


def parse_dns_settings(dns_settings: Dict) -> Dict:
    dns_info = {
        'provider': '未知',
        'custom_records': [],
        'google_domains_dns': False,
        'name_servers': []
    }

    if not dns_settings:
        return dns_info

    # 检查是否使用 Google Domains DNS
    if 'googleDomainsDns' in dns_settings:
        dns_info['google_domains_dns'] = True
        dns_info['provider'] = 'Google Domains DNS'
        # 从 googleDomainsDns 中提取名称服务器
        if 'dsRecords' in dns_settings.get('googleDomainsDns', {}):
            for ds_record in dns_settings['googleDomainsDns']['dsRecords']:
                if 'digest' in ds_record:
                    dns_info['name_servers'].append(f"DS记录: {ds_record.get('digest', '')}")

    # 检查是否使用自定义 DNS
    if 'customDns' in dns_settings:
        dns_info['provider'] = '自定义 DNS'
        custom_dns = dns_settings['customDns']

        # 提取名称服务器
        if 'nameServers' in custom_dns:
            dns_info['name_servers'] = custom_dns['nameServers']

        # 提取自定义 DNS 记录
        if 'records' in custom_dns:
            for record in custom_dns['records']:
                record_info = {
                    'name': record.get('name', ''),
                    'type': record.get('type', ''),
                    'ttl': record.get('ttl', ''),
                    'rrdata': record.get('rrdata', [])
                }
                dns_info['custom_records'].append(record_info)

    return dns_info


def get_cloud_dns_records(project_id: str, domain_name: str, credentials) -> Dict:
    """获取 Cloud DNS 中域名的实际 DNS 记录"""
    try:
        # 使用 Cloud DNS API
        dns_service = build('dns', 'v1', credentials=credentials)

        # 获取所有托管区域
        zones_response = dns_service.managedZones().list(project=project_id).execute()
        zones = zones_response.get('managedZones', [])

        matching_zone = None

        # 查找最匹配的托管区域
        for zone in zones:
            zone_dns_name = zone['dnsName'].rstrip('.')
            domain_parts = domain_name.lower().split('.')
            zone_parts = zone_dns_name.lower().split('.')

            # 检查是否匹配（从右向左匹配）
            if (len(zone_parts) <= len(domain_parts) and
                domain_parts[-len(zone_parts):] == zone_parts):
                if matching_zone is None or len(zone_parts) > len(matching_zone['dnsName'].rstrip('.').split('.')):
                    matching_zone = zone

        if not matching_zone:
            return {
                'zone_found': False,
                'zone_name': None,
                'dns_records': [],
                'error': f'未找到匹配的托管区域 for {domain_name}'
            }

        print(f"  为域名 {domain_name} 找到托管区域: {matching_zone['dnsName']}")

        # 获取托管区域的资源记录集
        zone_name = matching_zone['name']
        records_response = dns_service.resourceRecordSets().list(
            project=project_id,
            managedZone=zone_name
        ).execute()

        records = records_response.get('rrsets', [])

        # 分类记录
        dns_records = {
            'A_records': [],
            'AAAA_records': [],
            'CNAME_records': [],
            'MX_records': [],
            'TXT_records': [],
            'NS_records': [],
            'SOA_record': None,
            'other_records': []
        }

        for record in records:
            if record.get('type') == 'SOA' and not dns_records['SOA_record']:
                dns_records['SOA_record'] = {
                    'name': record.get('name', ''),
                    'type': record.get('type', ''),
                    'ttl': record.get('ttl', ''),
                    'rrdatas': record.get('rrdatas', [])
                }
            elif record.get('type') == 'A':
                dns_records['A_records'].append({
                    'name': record.get('name', ''),
                    'type': record.get('type', ''),
                    'ttl': record.get('ttl', ''),
                    'rrdatas': record.get('rrdatas', [])
                })
            elif record.get('type') == 'AAAA':
                dns_records['AAAA_records'].append({
                    'name': record.get('name', ''),
                    'type': record.get('type', ''),
                    'ttl': record.get('ttl', ''),
                    'rrdatas': record.get('rrdatas', [])
                })
            elif record.get('type') == 'CNAME':
                dns_records['CNAME_records'].append({
                    'name': record.get('name', ''),
                    'type': record.get('type', ''),
                    'ttl': record.get('ttl', ''),
                    'rrdatas': record.get('rrdatas', [])
                })
            elif record.get('type') == 'MX':
                dns_records['MX_records'].append({
                    'name': record.get('name', ''),
                    'type': record.get('type', ''),
                    'ttl': record.get('ttl', ''),
                    'rrdatas': record.get('rrdatas', [])
                })
            elif record.get('type') == 'TXT':
                dns_records['TXT_records'].append({
                    'name': record.get('name', ''),
                    'type': record.get('type', ''),
                    'ttl': record.get('ttl', ''),
                    'rrdatas': record.get('rrdatas', [])
                })
            elif record.get('type') == 'NS':
                dns_records['NS_records'].append({
                    'name': record.get('name', ''),
                    'type': record.get('type', ''),
                    'ttl': record.get('ttl', ''),
                    'rrdatas': record.get('rrdatas', [])
                })
            else:
                dns_records['other_records'].append({
                    'name': record.get('name', ''),
                    'type': record.get('type', ''),
                    'ttl': record.get('ttl', ''),
                    'rrdatas': record.get('rrdatas', [])
                })

        return {
            'zone_found': True,
            'zone_name': matching_zone['dnsName'],
            'dns_records': dns_records,
            'total_records': sum(len(records) for records in dns_records.values()) if isinstance(dns_records, dict) else 0,
            'error': None
        }

    except Exception as e:
        return {
            'zone_found': False,
            'zone_name': None,
            'dns_records': [],
            'error': f'获取 DNS 记录时出错: {str(e)}'
        }


def get_detailed_dns_records(project_id: str, domain_name: str, credentials) -> Dict:
    """获取 Cloud DNS 中域名的详细 DNS 记录"""
    try:
        # 使用 Cloud DNS API
        dns_service = build('dns', 'v1', credentials=credentials)

        # 获取所有托管区域
        zones_response = dns_service.managedZones().list(project=project_id).execute()
        zones = zones_response.get('managedZones', [])

        matching_zone = None

        # 查找最匹配的托管区域
        for zone in zones:
            zone_dns_name = zone['dnsName'].rstrip('.')
            domain_parts = domain_name.lower().split('.')
            zone_parts = zone_dns_name.lower().split('.')

            # 检查是否匹配（从右向左匹配）
            if (len(zone_parts) <= len(domain_parts) and
                domain_parts[-len(zone_parts):] == zone_parts):
                if matching_zone is None or len(zone_parts) > len(matching_zone['dnsName'].rstrip('.').split('.')):
                    matching_zone = zone

        if not matching_zone:
            return {
                'zone_found': False,
                'zone_name': None,
                'detailed_records': [],
                'error': f'未找到匹配的托管区域 for {domain_name}'
            }

        print(f"  为域名 {domain_name} 找到托管区域: {matching_zone['dnsName']}")

        # 获取托管区域的资源记录集
        zone_name = matching_zone['name']
        records_response = dns_service.resourceRecordSets().list(
            project=project_id,
            managedZone=zone_name
        ).execute()

        records = records_response.get('rrsets', [])
        detailed_records = []

        for record in records:
            record_info = {
                'name': record.get('name', '').rstrip('.'),
                'type': record.get('type', ''),
                'ttl': record.get('ttl', ''),
                'rrdatas': record.get('rrdatas', [])
            }
            detailed_records.append(record_info)

        return {
            'zone_found': True,
            'zone_name': matching_zone['dnsName'],
            'detailed_records': detailed_records,
            'total_records': len(detailed_records),
            'error': None
        }

    except Exception as e:
        return {
            'zone_found': False,
            'zone_name': None,
            'detailed_records': [],
            'error': f'获取 DNS 记录时出错: {str(e)}'
        }


def main():
    parser = argparse.ArgumentParser(description='获取 india-game-pro 项目的所有域名和DNS信息')
    parser.add_argument('-c', '--credentials', default=None, help='服务账户密钥JSON文件路径')
    parser.add_argument('--mode', choices=['service_account', 'gcloud'], default='service_account',
                       help='认证模式: service_account 使用JSON密钥; gcloud 使用当前gcloud用户访问令牌')
    parser.add_argument('--output-format', choices=['detailed', 'simple', 'csv', 'dns-detailed'], default='detailed',
                       help='输出格式: detailed(详细), simple(简化), csv(CSV文件), dns-detailed(DNS记录详细)')
    parser.add_argument('--skip-dns-records', action='store_true',
                       help='跳过 Cloud DNS 记录查询，只获取域名注册信息')
    args = parser.parse_args()

    # 认证选择，基于 list_ips.py 的模式逻辑
    if args.mode == 'gcloud':
        print('模式: gcloud (使用当前 gcloud 用户访问令牌)')
        try:
            token_result = subprocess.run(
                'gcloud auth print-access-token',
                shell=True,
                check=True,
                capture_output=True,
                text=True,
            )
            access_token = token_result.stdout.strip()
            if not access_token:
                raise RuntimeError('无法从 gcloud 获取访问令牌。')
        except subprocess.CalledProcessError as e:
            print('错误: 无法从 gcloud 获取访问令牌。')
            print(e.stderr)
            return
        credentials = UserCredentials(token=access_token)
        print('使用 gcloud 用户凭据。')
    else:
        print('模式: service_account')
        credentials_path = resolve_credentials_path(args.credentials)
        if not credentials_path:
            print('错误: 未找到凭据文件。提供 --credentials 或设置 GOOGLE_APPLICATION_CREDENTIALS。')
            return
        print(f'使用凭据文件: {credentials_path}')
        credentials = service_account.Credentials.from_service_account_file(credentials_path)

        # 显示服务账户邮箱（如果可用）
        sa_email = None
        try:
            sa_email = getattr(credentials, 'service_account_email', None)
        except Exception:
            sa_email = None
        if not sa_email:
            try:
                with open(credentials_path, 'r') as f:
                    sa_email = json.load(f).get('client_email')
            except Exception:
                sa_email = None
        print(f"服务账户: {sa_email or '<未知>'}")

    # 指定目标项目
    project_id = 'india-game-pro'

    try:
        # 获取 Cloud Domains 注册信息
        registrations = list_cloud_domains(project_id, credentials)
        if not registrations:
            print("未找到 Cloud Domains 注册。")
            return

        # 如果需要，获取每个域名的 Cloud DNS 记录
        if not args.skip_dns_records:
            print(f"正在获取 {len(registrations)} 个域名的 Cloud DNS 记录...")
            for reg in registrations:
                domain_name = reg.get('domain_name')
                if domain_name:
                    print(f"  获取 {domain_name} 的 DNS 记录...")
                    dns_record_info = get_cloud_dns_records(project_id, domain_name, credentials)
                    reg['cloud_dns_zone'] = dns_record_info.get('zone_name')
                    reg['cloud_dns_records'] = json.dumps(dns_record_info.get('dns_records', {}))
                    reg['dns_records_count'] = dns_record_info.get('total_records', 0)
                    reg['dns_error'] = dns_record_info.get('error')

                    # 获取详细 DNS 记录（用于 csv 和 dns-detailed 模式）
                    if args.output_format in ['csv', 'dns-detailed']:
                        detailed_info = get_detailed_dns_records(project_id, domain_name, credentials)
                        reg['detailed_dns_records'] = json.dumps(detailed_info.get('detailed_records', []))
                        if detailed_info.get('error'):
                            reg['dns_error'] = detailed_info.get('error')

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')

        if args.output_format == 'dns-detailed':
            # DNS 记录详细输出
            print(f"\n{'='*100}")
            print(f"项目: {project_id} - 详细 DNS 记录信息")
            print(f"{'='*100}")

            for i, reg in enumerate(registrations, 1):
                domain_name = reg.get('domain_name', 'N/A')
                print(f"\n{i}. 域名: {domain_name}")

                # 显示 Cloud DNS 托管区域
                dns_zone = reg.get('cloud_dns_zone')
                if dns_zone:
                    print(f"   托管区域: {dns_zone}")

                # 显示具体 DNS 记录
                detailed_records = reg.get('detailed_dns_records', '[]')
                if detailed_records and detailed_records != '[]':
                    try:
                        records = json.loads(detailed_records)
                        if records:
                            for record in records:
                                name = record.get('name', '')
                                record_type = record.get('type', '')
                                ttl = record.get('ttl', '')
                                rrdatas = record.get('rrdatas', [])

                                # 格式化输出
                                if name == domain_name + '.':
                                    name = domain_name  # 根记录显示为完整域名

                                # 只显示 A 和 CNAME 记录
                                if record_type not in ['A', 'CNAME']:
                                    continue

                                if rrdatas:
                                    print(f"     {name}  {', '.join(rrdatas)}")
                        else:
                            print(f"     {domain_name}  无解析记录")
                    except Exception as e:
                        print(f"     解析记录时出错: {e}")

                # 显示错误信息
                dns_error = reg.get('dns_error')
                if dns_error:
                    print(f"   错误: {dns_error}")

            print(f"\n{'='*100}")
            print(f"总计: {len(registrations)} 个域名")
            print(f"{'='*100}")
        elif args.output_format == 'csv':
            # 输出详细 DNS 记录到 CSV
            csv_filename = f"india_game_pro_domains_dns_detailed_{ts}.csv"
            with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'project_id', 'domain_name', 'state', 'expire_time', 'contact_privacy',
                    'dns_provider', 'cloud_dns_zone', 'record_name', 'record_type', 'ttl', 'record_value'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                writer.writeheader()

                for reg in registrations:
                    domain_name = reg.get('domain_name', '')
                    project_id = reg.get('project_id', 'india-game-pro')
                    state = reg.get('state', '')
                    expire_time = reg.get('expire_time', '')
                    contact_privacy = reg.get('contact_privacy', '')
                    dns_provider = reg.get('dns_provider', '')
                    dns_zone = reg.get('cloud_dns_zone', '')

                    detailed_records = reg.get('detailed_dns_records', '[]')
                    if detailed_records and detailed_records != '[]':
                        try:
                            records = json.loads(detailed_records)
                            for record in records:
                                name = record.get('name', '')
                                record_type = record.get('type', '')
                                ttl = record.get('ttl', '')
                                rrdatas = record.get('rrdatas', [])

                                # 格式化记录名称
                                if name == domain_name + '.':
                                    name = domain_name

                                # 只保留 A 和 CNAME 记录
                                if record_type not in ['A', 'CNAME']:
                                    continue

                                if rrdatas:
                                    for rrdata in rrdatas:
                                        writer.writerow({
                                            'project_id': project_id,
                                            'domain_name': domain_name,
                                            'state': state,
                                            'expire_time': expire_time,
                                            'contact_privacy': contact_privacy,
                                            'dns_provider': dns_provider,
                                            'cloud_dns_zone': dns_zone,
                                            'record_name': name,
                                            'record_type': record_type,
                                            'ttl': ttl,
                                            'record_value': rrdata,
                                        })
                        except Exception as e:
                            writer.writerow({
                                'project_id': project_id,
                                'domain_name': domain_name,
                                'state': state,
                                'expire_time': expire_time,
                                'contact_privacy': contact_privacy,
                                'dns_provider': dns_provider,
                                'cloud_dns_zone': dns_zone,
                                'record_name': '',
                                'record_type': 'ERROR',
                                'ttl': '',
                                'record_value': f'解析记录时出错: {e}',
                            })

            print(f"✅ 已保存 {len(registrations)} 个域名的详细 DNS 记录到: {csv_filename}")

            # 同时生成包含基本信息的 CSV 文件
            df = pd.DataFrame(registrations)
            cols = ['project_id', 'domain_name', 'state', 'expire_time', 'contact_privacy',
                   'dns_provider', 'name_servers', 'custom_dns_records', 'google_domains_dns',
                   'cloud_dns_zone', 'dns_records_count', 'dns_error']
            df = df[[c for c in cols if c in df.columns]]

            out_csv = f"india_game_pro_domains_with_dns_{ts}.csv"
            df.to_csv(out_csv, index=False)
            print(f"✅ 已保存 {len(df)} 个域名注册基本信息到: {out_csv}")
        elif args.output_format == 'simple':
            # 输出简化信息
            print(f"\n{'='*100}")
            print(f"项目: {project_id} - 域名注册摘要（包含 DNS 信息）")
            print(f"{'='*100}")
            print(f"{'域名':<35} {'状态':<10} {'DNS提供商':<15} {'DNS区域':<25} {'记录数':<8} {'到期时间':<12}")
            print(f"{'-'*100}")

            for reg in registrations:
                domain = reg.get('domain_name', 'N/A')
                state = reg.get('state', 'N/A')
                dns_provider = reg.get('dns_provider', 'N/A')
                dns_zone = reg.get('cloud_dns_zone', 'N/A') if reg.get('cloud_dns_zone') else 'N/A'
                records_count = reg.get('dns_records_count', 0)
                expire_time = reg.get('expire_time', 'N/A')
                if expire_time != 'N/A' and 'T' in expire_time:
                    expire_time = expire_time.split('T')[0]

                # 确保所有字段都有有效值
                domain = str(domain) if domain else 'N/A'
                state = str(state) if state else 'N/A'
                dns_provider = str(dns_provider) if dns_provider else 'N/A'
                dns_zone = str(dns_zone) if dns_zone else 'N/A'
                expire_time = str(expire_time) if expire_time else 'N/A'

                print(f"{domain:<35} {state:<10} {dns_provider:<15} {dns_zone:<25} {records_count:<8} {expire_time:<12}")
        else:
            # 详细输出
            print(f"\n{'='*120}")
            print(f"项目: {project_id} - 详细域名信息（包含 Cloud DNS 记录）")
            print(f"{'='*120}")

            for i, reg in enumerate(registrations, 1):
                print(f"\n{i}. 域名: {reg.get('domain_name', 'N/A')}")
                print(f"   状态: {reg.get('state', 'N/A')}")
                print(f"   DNS 提供商: {reg.get('dns_provider', 'N/A')}")
                print(f"   到期时间: {reg.get('expire_time', 'N/A')}")
                print(f"   联系人隐私: {reg.get('contact_privacy', 'N/A')}")

                # 显示 Cloud DNS 信息
                dns_zone = reg.get('cloud_dns_zone')
                if dns_zone:
                    print(f"   Cloud DNS 托管区域: {dns_zone}")

                records_count = reg.get('dns_records_count', 0)
                if records_count > 0:
                    print(f"   DNS 记录总数: {records_count}")

                dns_error = reg.get('dns_error')
                if dns_error:
                    print(f"   DNS 记录错误: {dns_error}")

                # 显示名称服务器
                name_servers = reg.get('name_servers', [])
                if name_servers:
                    print("   名称服务器:")
                    for ns in json.loads(name_servers):
                        print(f"     - {ns}")

                # 显示 Cloud DNS 记录摘要
                cloud_dns_records = reg.get('cloud_dns_records', '{}')
                if cloud_dns_records and cloud_dns_records != '{}':
                    try:
                        dns_data = json.loads(cloud_dns_records)
                        if isinstance(dns_data, dict):
                            print("   Cloud DNS 记录:")
                            if dns_data.get('A_records'):
                                print(f"     - A 记录: {len(dns_data['A_records'])} 条")
                            if dns_data.get('AAAA_records'):
                                print(f"     - AAAA 记录: {len(dns_data['AAAA_records'])} 条")
                            if dns_data.get('CNAME_records'):
                                print(f"     - CNAME 记录: {len(dns_data['CNAME_records'])} 条")
                            if dns_data.get('MX_records'):
                                print(f"     - MX 记录: {len(dns_data['MX_records'])} 条")
                            if dns_data.get('TXT_records'):
                                print(f"     - TXT 记录: {len(dns_data['TXT_records'])} 条")
                            if dns_data.get('NS_records'):
                                print(f"     - NS 记录: {len(dns_data['NS_records'])} 条")
                    except:
                        pass

                # 显示 Google Domains DNS 信息
                if reg.get('google_domains_dns'):
                    print("   使用 Google Domains DNS 托管")

            print(f"\n{'='*120}")
            print(f"总计: {len(registrations)} 个域名注册")
            print(f"{'='*120}")

    except Exception as e:
        print(f"发生错误: {e}")


if __name__ == '__main__':
    main()
