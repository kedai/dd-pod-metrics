#!/usr/bin/env python3
from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v1.api.metrics_api import MetricsApi
from dotenv import load_dotenv
import os
from datetime import datetime

def validate_datadog_credentials():
    """Validate Datadog API and Application keys."""
    load_dotenv()
    
    # Check API Key first (required for all operations)
    api_key_id = os.getenv('DD_API_KEY_ID')
    api_key = os.getenv('DD_API_KEY')
    app_key = os.getenv('DD_APP_KEY')
    
    if not api_key:
        print("❌ Error: DD_API_KEY not found in .env file")
        print("API Key is required for all Datadog API operations")
        return False
        
    if not app_key:
        print("\n❌ Error: DD_APP_KEY not found in .env file")
        print("Application Key is required for querying metrics")
        print("Visit: https://app.datadoghq.com/organization-settings/application-keys to create one")
        return False
    
    # Configure the client
    configuration = Configuration()
    configuration.api_key['apiKeyAuth'] = api_key
    configuration.api_key['appKeyAuth'] = app_key
    
    try:
        with ApiClient(configuration) as api_client:
            # Try to query metrics API specifically since that's what we'll be using
            metrics_api = MetricsApi(api_client)
            from_time = int(datetime.now().timestamp()) - 300  # 5 minutes ago
            to_time = int(datetime.now().timestamp())
            
            # Try a simple metric query
            metrics_api.query_metrics(
                _from=from_time,
                to=to_time,
                query="avg:system.cpu.user{*}"
            )
            
            print("\n✅ Success: Datadog credentials are valid!")
            print(f"API Key ID: {api_key_id}")
            print("✓ Successfully tested metrics query")
            return True
    except Exception as e:
        print(f"\n❌ Error: Failed to validate credentials: {str(e)}")
        return False

if __name__ == "__main__":
    validate_datadog_credentials()
