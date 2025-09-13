import os
import json
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
        print(f"Found credentials in environment variable: {env_override}")
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
            print(f"Found credentials at common path: {p}")
            return p

    return None


def list_cloud_domains(project_id: str, credentials) -> List[Dict]:
    """Lists Cloud Domains registrations for a given project.

    Uses the Cloud Domains API: projects.locations.registrations.list
    Parent format: projects/{project}/locations/-
    """
    print(f"Listing Cloud Domains for project: {project_id}")
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

            results.append({
                'project_id': project_id,
                'registration_name': name_full,
                'domain_name': domain,
                'state': state,
                'expire_time': expire_time,
                'contact_privacy': contact_privacy,
                'dns_settings': json.dumps(dns_settings),
                'management_settings': json.dumps(mgmt),
            })
        request = service.projects().locations().registrations().list_next(
            previous_request=request, previous_response=response
        )

    print(f"Found {len(results)} registrations in {project_id}.")
    return results


def main():
    parser = argparse.ArgumentParser(description='List Cloud Domains registrations for a GCP project.')
    parser.add_argument('-p', '--project', required=True, help='GCP project ID (e.g., india-game-pro)')
    parser.add_argument('-c', '--credentials', default=None, help='Path to the service account key JSON')
    parser.add_argument('--mode', choices=['service_account', 'gcloud'], default='service_account', help='Auth mode: service_account uses a JSON key; gcloud uses current gcloud user access token')
    parser.add_argument('--simple', action='store_true', help='Also輸出簡化版欄位：狀態, 網域名稱, DNS, 續購, 到期日')
    args = parser.parse_args()

    # Auth selection modeled after list_ips.py (only the mode logic is referenced)
    if args.mode == 'gcloud':
        print('Mode: gcloud (use current gcloud user access token)')
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
                raise RuntimeError('Failed to obtain access token from gcloud.')
        except subprocess.CalledProcessError as e:
            print('Error: Failed to get access token from gcloud.')
            print(e.stderr)
            return
        credentials = UserCredentials(token=access_token)
        print('Using gcloud user credentials.')
    else:
        print('Mode: service_account')
        credentials_path = resolve_credentials_path(args.credentials)
        if not credentials_path:
            print('Error: Credentials file not found. Provide --credentials or set GOOGLE_APPLICATION_CREDENTIALS.')
            return
        print(f'Using credentials file: {credentials_path}')
        credentials = service_account.Credentials.from_service_account_file(credentials_path)

        # Show service account email if available
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
        print(f"Service Account: {sa_email or '<unknown>'}")

    try:
        registrations = list_cloud_domains(args.project, credentials)
        if not registrations:
            print("No Cloud Domains registrations found.")
            return

        df = pd.DataFrame(registrations)
        # Order columns
        cols = ['project_id', 'domain_name', 'state', 'expire_time', 'contact_privacy', 'registration_name', 'dns_settings']
        df = df[[c for c in cols if c in df.columns]]

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        if not args.simple:
            out_csv = f"gcp_domains_{args.project}_{ts}.csv"
            df.to_csv(out_csv, index=False)
            print(f"Saved {len(df)} registrations to {out_csv}")

        if args.simple:
            # 產出簡化版
            def map_state(s: str) -> str:
                mapping = {
                    'ACTIVE': '有效',
                    'EXPORTED': '已匯出',
                    'EXPIRED': '已過期',
                    'REGISTRATION_PENDING': '註冊處理中',
                    'REGISTRATION_FAILED': '註冊失敗',
                    'TRANSFER_PENDING': '移轉處理中',
                    'TRANSFER_FAILED': '移轉失敗',
                    'SUSPENDED': '已暫停',
                }
                return mapping.get(s or '', s or '')

            def detect_dns(dns_json: str) -> str:
                try:
                    ds = json.loads(dns_json) if dns_json else {}
                except Exception:
                    ds = {}
                if 'customDns' in ds:
                    return 'Cloud DNS'
                if 'googleDomainsDns' in ds:
                    return 'Google Domains DNS'
                return '-'

            def map_renewal(mgmt_json: str) -> str:
                try:
                    mg = json.loads(mgmt_json) if mgmt_json else {}
                    rm = mg.get('renewalMethod')
                except Exception:
                    rm = None
                mapping = {
                    'AUTOMATIC_RENEWAL': '自動',
                    'MANUAL_RENEWAL': '手動',
                    'RENEWAL_DISABLED': '停用',
                }
                return mapping.get(rm or '', rm or '-')

            def format_expire(t: str) -> str:
                if not t:
                    return '-'
                # 取日期部分
                date_part = (t.split('T')[0]).strip()
                try:
                    y, m, d = date_part.split('-')
                    return f"{int(y)}年{int(m)}月{int(d)}日"
                except Exception:
                    return date_part

            simple_rows = []
            for item in registrations:
                simple_rows.append({
                    '狀態': map_state(item.get('state')),
                    '網域名稱': item.get('domain_name') or '-',
                    'DNS': detect_dns(item.get('dns_settings')),
                    '續購': map_renewal(item.get('management_settings')),
                    '到期日': format_expire(item.get('expire_time')),
                })

            df_simple = pd.DataFrame(simple_rows, columns=['狀態', '網域名稱', 'DNS', '續購', '到期日'])
            out_simple = f"gcp_domains_simple_{args.project}_{ts}.csv"
            df_simple.to_csv(out_simple, index=False)
            print(f"Saved simplified view to {out_simple}")

        # Also print a concise summary
        for row in registrations:
            print(f"- {row.get('domain_name') or row.get('registration_name')} | state={row.get('state')} | expire={row.get('expire_time')}")

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == '__main__':
    main()
