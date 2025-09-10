import os
import json
import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build
import subprocess
from google.cloud import service_usage_v1
import argparse
import google.auth
from google.oauth2.credentials import Credentials as UserCredentials
from datetime import datetime

def resolve_credentials_path(cli_path=None):
    """Resolves the credentials path in a robust way."""
    # 1. Prioritize the path from the command-line argument
    if cli_path and os.path.isfile(cli_path):
        return cli_path

    # 2. Check common environment variables
    env_override = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("GCP_SA_KEY_PATH")
    if env_override and os.path.isfile(env_override):
        print(f"Found credentials in environment variable: {env_override}")
        return env_override

    # 3. Check common relative and absolute paths
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
            
    return None # Return None if not found

def get_active_gcloud_account():
    """Returns the active gcloud account email, or None if unavailable."""
    try:
        result = subprocess.run(
            'gcloud auth list --format="value(account)" --filter=status:ACTIVE',
            shell=True,
            check=True,
            capture_output=True,
            text=True,
        )
        acct = result.stdout.strip()
        return acct if acct else None
    except subprocess.CalledProcessError:
        return None

def get_all_projects(credentials_override_path=None):
    """Lists all projects using the gcloud command.
    If credentials_override_path is provided, uses that key via CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE.
    Otherwise, uses the current active gcloud user account (ADC).
    """
    print("Fetching all accessible projects using 'gcloud'...")
    try:
        my_env = os.environ.copy()
        if credentials_override_path:
            if not os.path.isfile(credentials_override_path):
                raise FileNotFoundError("Could not find service account key file.")
            # Use an environment variable to specify credentials for this command only
            my_env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = credentials_override_path

        list_command = 'gcloud projects list --format="value(projectId)"'
        result = subprocess.run(
            list_command, 
            shell=True, 
            check=True, 
            capture_output=True, 
            text=True, 
            env=my_env
        )
        project_ids = result.stdout.strip().split('\n')
        print(f"Found {len(project_ids)} projects.")
        return project_ids
    except subprocess.CalledProcessError as e:
        print(f"Error executing gcloud command: {e}")
        print(f"Stderr: {e.stderr}")
        return []
    except FileNotFoundError:
        print("Error: 'gcloud' command not found. Please ensure the Google Cloud SDK is installed and in your PATH.")
        return []

def is_api_enabled(project_id, credentials):
    """Checks if the Compute Engine API is enabled for a given project."""
    client = service_usage_v1.ServiceUsageClient(credentials=credentials)
    api_name = f"projects/{project_id}/services/compute.googleapis.com"
    try:
        request = service_usage_v1.GetServiceRequest(name=api_name)
        service = client.get_service(request=request)
        return service.state == service_usage_v1.State.ENABLED
    except Exception as e:
        print(f"Could not check API status for {project_id}. Error: {e}")
        return False

def get_ips_for_project(project_id, compute_service):
    """Gets all IP addresses for a single project."""
    all_ips = {}
    print(f"\n--- Processing project: {project_id} ---")

    # 1. Get all static addresses
    addr_request = compute_service.addresses().aggregatedList(project=project_id)
    while addr_request is not None:
        response = addr_request.execute()
        for region, addresses_scoped_list in response.get('items', {}).items():
            if 'addresses' in addresses_scoped_list:
                for address in addresses_scoped_list['addresses']:
                    ip = address.get('address')
                    if ip:
                        all_ips[ip] = {
                            'project_id': project_id,
                            'name': address.get('name', 'N/A'),
                            'ip_address': ip,
                            'access_type': address.get('addressType', 'N/A'),
                            'region': region.replace('regions/', ''),
                            'status': address.get('status', 'N/A'),
                            'ip_version': address.get('ipVersion', 'N/A'),
                            'user': address.get('users', ['N/A'])[0].split('/')[-1],
                            'source': 'Static Address'
                        }
        addr_request = compute_service.addresses().aggregatedList_next(previous_request=addr_request, previous_response=response)

    # 2. Get all VM instance addresses
    instance_request = compute_service.instances().aggregatedList(project=project_id)
    while instance_request is not None:
        response = instance_request.execute()
        for zone, instances_scoped_list in response.get('items', {}).items():
            if 'instances' in instances_scoped_list:
                for instance in instances_scoped_list['instances']:
                    for network_interface in instance.get('networkInterfaces', []):
                        # Internal IP
                        internal_ip = network_interface.get('networkIP')
                        if internal_ip and internal_ip not in all_ips:
                            all_ips[internal_ip] = {
                                'project_id': project_id,
                                'name': instance.get('name', 'N/A'),
                                'ip_address': internal_ip,
                                'access_type': 'INTERNAL',
                                'region': zone.split('/')[-1][:-2],
                                'status': instance.get('status', 'N/A'),
                                'ip_version': 'IPv4',
                                'user': f"VM Instance {instance.get('name')}",
                                'source': 'VM Internal'
                            }
                        # External IP
                        for access_config in network_interface.get('accessConfigs', []):
                            external_ip = access_config.get('natIP')
                            if external_ip and external_ip not in all_ips:
                                all_ips[external_ip] = {
                                    'project_id': project_id,
                                    'name': instance.get('name', 'N/A'),
                                    'ip_address': external_ip,
                                    'access_type': 'EXTERNAL',
                                    'region': zone.split('/')[-1][:-2],
                                    'status': instance.get('status', 'N/A'),
                                    'ip_version': 'IPv4',
                                    'user': f"VM Instance {instance.get('name')}",
                                    'source': 'VM External'
                                }
        instance_request = compute_service.instances().aggregatedList_next(previous_request=instance_request, previous_response=response)

    # 3. Get all Forwarding Rule addresses
    fr_request = compute_service.forwardingRules().aggregatedList(project=project_id)
    while fr_request is not None:
        response = fr_request.execute()
        for region, forwarding_rules_scoped_list in response.get('items', {}).items():
            if 'forwardingRules' in forwarding_rules_scoped_list:
                for rule in forwarding_rules_scoped_list['forwardingRules']:
                    ip = rule.get('IPAddress')
                    if ip and ip not in all_ips:
                        all_ips[ip] = {
                            'project_id': project_id,
                            'name': rule.get('name', 'N/A'),
                            'ip_address': ip,
                            'access_type': 'EXTERNAL',
                            'region': region.replace('regions/', ''),
                            'status': 'IN_USE',
                            'ip_version': rule.get('IPProtocol', 'N/A'),
                            'user': f"Forwarding Rule {rule.get('name')}",
                            'source': 'Forwarding Rule'
                        }
        fr_request = compute_service.forwardingRules().aggregatedList_next(previous_request=fr_request, previous_response=response)

    print(f"Found {len(all_ips)} unique IP addresses in {project_id}.")
    return list(all_ips.values())

def main():
    """Main function to orchestrate fetching IPs from all projects."""
    parser = argparse.ArgumentParser(description='GCP IP Address Collector.')
    parser.add_argument(
        '-c', '--credentials',
        help='Path to the GCP service account credentials JSON file.',
        default=None
    )
    # Deprecated in favor of --mode, but kept for backward compatibility
    parser.add_argument(
        '--auth', choices=['service_account', 'gcloud'], default=None,
        help='[Deprecated] Use --mode instead. Authentication source: service_account uses a JSON key; gcloud uses your active gcloud user.'
    )
    parser.add_argument(
        '--mode', choices=['service_account', 'gcloud'], default='service_account',
        help='Execution mode: service_account uses a JSON key; gcloud uses your current gcloud user (no extra login needed).'
    )
    args = parser.parse_args()

    try:
        # Determine mode: --mode takes precedence; fallback to --auth if provided
        mode = args.mode if args.mode else (args.auth or 'service_account')
        print(f"Mode: {mode}")
        if mode == 'gcloud':
            active_acct = get_active_gcloud_account()
            if active_acct:
                print(f"Gcloud Account: {active_acct}")
            else:
                print("Gcloud Account: <unknown>")
            print("Using gcloud user credentials (access token from gcloud auth).")
            # Get an access token from the active gcloud account to avoid requiring ADC login
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
                print("Error: Failed to get access token from gcloud.\nStderr:")
                print(e.stderr)
                return

            credentials = UserCredentials(token=access_token)
            project_ids = get_all_projects(credentials_override_path=None)
        else:
            credentials_path = resolve_credentials_path(args.credentials)
            if not credentials_path:
                print("Error: Credentials file not found.")
                print("Please provide the path using the --credentials flag, or set the GOOGLE_APPLICATION_CREDENTIALS environment variable.")
                return
            print(f"Using credentials file: {credentials_path}")
            credentials = service_account.Credentials.from_service_account_file(credentials_path)
            # Try to show service account email
            sa_email = None
            try:
                sa_email = getattr(credentials, 'service_account_email', None)
            except Exception:
                sa_email = None
            if not sa_email:
                try:
                    with open(credentials_path, 'r') as f:
                        key_info = json.load(f)
                        sa_email = key_info.get('client_email')
                except Exception:
                    sa_email = None
            if sa_email:
                print(f"Service Account: {sa_email}")
            else:
                print("Service Account: <unknown>")
            project_ids = get_all_projects(credentials_override_path=credentials_path)
        project_statuses = []
        compute_service = build('compute', 'v1', credentials=credentials)

        all_project_ips = []
        for project_id in project_ids:
            api_enabled = is_api_enabled(project_id, credentials)
            if api_enabled:
                project_statuses.append({'project_id': project_id, 'compute_api_status': 'ENABLED'})
                project_ips = get_ips_for_project(project_id, compute_service)
                if project_ips:
                    all_project_ips.extend(project_ips)
            else:
                print(f"\n--- Skipping project: {project_id} (Compute Engine API not enabled) ---")
                project_statuses.append({'project_id': project_id, 'compute_api_status': 'DISABLED'})

        # Save all collected IPs to a single file
        if all_project_ips:
            ip_df = pd.DataFrame(all_project_ips)
            # Optimize column order, keeping project_id for identification
            optimized_columns = [
                'project_id', 'ip_address', 'name', 'access_type', 'status', 'region',
                'source', 'user', 'ip_version'
            ]
            final_columns = [col for col in optimized_columns if col in ip_df.columns]
            ip_df = ip_df[final_columns]

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            ip_filename = f'gcp_all_ips_{timestamp}.csv'
            ip_df.to_csv(ip_filename, index=False)
            print(f"\nSuccessfully saved a total of {len(ip_df)} IP addresses to {ip_filename}")

        # Create and save the project status report
        if project_statuses:
            status_df = pd.DataFrame(project_statuses)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            status_filename = f'gcp_projects_status_{timestamp}.csv'
            status_df.to_csv(status_filename, index=False)
            print(f"\nSuccessfully saved status of {len(status_df)} projects to {status_filename}")

    except FileNotFoundError:
        print(f"Error: Credentials file not found at '{credentials_path}'.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    main()

