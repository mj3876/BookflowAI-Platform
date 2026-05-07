# functions/sync-secret/__init__.py
# Key Vault SecretNewVersionCreated    AWS Secrets Manager 
# VPN  : AWS    
# VPN  : AWS_API_GATEWAY_URL      

import logging
import json
import os
import azure.functions as func
from azure.identity import ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient

def main(event: func.EventGridEvent) -> None:
    logging.info("Event Grid  ")

    try:
        event_data = event.get_json()
        secret_name = event_data.get("ObjectName", "")
        secret_version = event_data.get("Version", "")
        vault_name = event_data.get("VaultName", "")

        logging.info(f"   - : {secret_name}, : {secret_version}")

        #  ID  Key Vault 
        key_vault_uri = os.environ.get("KEY_VAULT_URI")
        credential = ManagedIdentityCredential(
            client_id=os.environ.get("AZURE_CLIENT_ID")
        )
        kv_client = SecretClient(vault_url=key_vault_uri, credential=credential)

        #   
        secret = kv_client.get_secret(secret_name)
        secret_value = secret.value
        logging.info(f"  : {secret_name}")

        # AWS API Gateway URL 
        aws_api_url = os.environ.get("AWS_API_GATEWAY_URL", "")

        if aws_api_url == "PLACEHOLDER-VPN-CONNECTED-LATER" or not aws_api_url:
            # VPN   — AWS  
            logging.warning(
                f"[VPN  ] AWS Secrets Manager   - "
                f": {secret_name} - "
                f"VPN   aws-api-gateway-url  "
            )
            return

        # VPN     
        import requests

        payload = {
            "secret_name": f"bookflow/azure/{secret_name}",
            "secret_value": secret_value
        }

        response = requests.post(
            aws_api_url,
            json=payload,
            timeout=10
        )

        if response.status_code == 200:
            logging.info(f"AWS Secrets Manager  : {secret_name}")
        else:
            logging.error(
                f"AWS   - : {response.status_code} - "
                f": {response.text}"
            )
            raise Exception(f"AWS API  : {response.status_code}")

    except Exception as e:
        logging.error(f" : {str(e)}")
        raise
