import os
import json
import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build
import subprocess
from google.cloud import service_usage_v1

def get_all_projects():
    """Lists all projects using the gcloud command without changing the active user account."""
    print("Fetching all accessible projects using 'gcloud'...")
    try:
        key_file = os.path.join(os.path.dirname(__file__), '..', 'incdr-infra.json')
        
        # Use an environment variable to specify credentials for this command only
        # This avoids changing the user's active gcloud configuration
        my_env = os.environ.copy()
        my_env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = key_file

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
    credentials_path = os.path.join(os.path.dirname(__file__), '..', 'incdr-infra.json')
    try:
        with open(credentials_path, 'r') as f:
            creds_json = json.load(f)
            client_email = creds_json.get('client_email', 'Service account email not found.')
            print(f"Running with service account: {client_email}")

        credentials = service_account.Credentials.from_service_account_file(credentials_path)
        
        project_ids = get_all_projects()
        project_statuses = []
        compute_service = build('compute', 'v1', credentials=credentials)

        for project_id in project_ids:
            api_enabled = is_api_enabled(project_id, credentials)
            if api_enabled:
                project_statuses.append({'project_id': project_id, 'compute_api_status': 'ENABLED'})
                project_ips = get_ips_for_project(project_id, compute_service)
                
                if project_ips:
                    ip_df = pd.DataFrame(project_ips)
                    # Optimize column order, removing project_id as it's in the filename
                    optimized_columns = [
                        'ip_address', 'name', 'access_type', 'status', 'region',
                        'source', 'user', 'ip_version'
                    ]
                    final_columns = [col for col in optimized_columns if col in ip_df.columns]
                    ip_df = ip_df[final_columns]
                    
                    ip_filename = f'{project_id}_ips.csv'
                    ip_df.to_csv(ip_filename, index=False)
                    print(f"Successfully saved {len(ip_df)} IP addresses for {project_id} to {ip_filename}")
            else:
                print(f"\n--- Skipping project: {project_id} (Compute Engine API not enabled) ---")
                project_statuses.append({'project_id': project_id, 'compute_api_status': 'DISABLED'})

        # Create and save the project status report
        if project_statuses:
            status_df = pd.DataFrame(project_statuses)
            status_filename = 'gcp_projects_status.csv'
            status_df.to_csv(status_filename, index=False)
            print(f"\nSuccessfully saved status of {len(status_df)} projects to {status_filename}")

    except FileNotFoundError:
        print(f"Error: Credentials file not found at '{credentials_path}'.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    main()

