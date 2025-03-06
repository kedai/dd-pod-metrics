# Kubernetes Pod Memory Usage Monitor

This script queries Datadog API to fetch memory and CPU usage statistics for Kubernetes pods.

## Design Decisions and Trade-offs

### 1. Metric Selection
- **Choice**: Using `kubernetes.memory.usage` and `kubernetes.cpu.usage.total` over alternatives
- **Why**: These metrics provide the most accurate real-time usage data
- **Trade-off**: Higher API load but better accuracy for capacity planning

### 2. Time Range Handling
- **Choice**: Offering both shortcuts (1h, 6h, 24h, 7d) and custom ranges
- **Why**: Balances ease of use with flexibility
- **Trade-off**: More code complexity for better user experience

### 3. Pod Status Detection
- **Choice**: Using CPU metrics to determine pod status
- **Why**: Most reliable way to detect truly active pods
- **Trade-off**: May miss some edge cases but works for most scenarios

### 4. Resource Calculations
- **Choice**: Showing both peak and average usage
- **Why**: Provides complete picture for resource planning
- **Trade-off**: More data to process but better insights

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up your Datadog credentials in a `.env` file:
```bash
DD_API_KEY_ID=your_api_key_id_here    # The ID of your API key (for identification)
DD_API_KEY=your_api_key_here          # The actual API key value (secret)
DD_APP_KEY=your_app_key_here          # Your application key
```

To get these credentials:
1. **API Key & ID**: 
   - Go to: Organization Settings → API Keys (https://app.datadoghq.com/organization-settings/api-keys)
   - Create or use existing key
   - Copy both the Key ID and Key value

2. **Application Key**:
   - Go to: Organization Settings → Application Keys (https://app.datadoghq.com/organization-settings/application-keys)
   - Create new key
   - Copy the key value (only shown once)

## Validation and Troubleshooting

1. **Validate Setup**
   ```bash
   python test_connection.py
   ```
   This will verify:
   - Environment variables are properly set
   - API credentials are valid
   - Datadog API is accessible

2. **Common Issues**
   - No clusters shown: Ensure your API key has access to Kubernetes metrics
   - Missing metrics: Check pod labels and resource limits are set
   - "N/A" values: Usually means the metric is not available for that pod

3. **Debugging Tips**
   - Use shorter time ranges (1h) first to validate data
   - Check pod status in Kubernetes if metrics seem incorrect
   - Verify resource limits are set if percentages show as N/A

## Metrics Used

The script uses several key Kubernetes metrics from Datadog to provide comprehensive resource usage information:

### Memory Metrics
1. `kubernetes.memory.usage`
   - What: Current memory usage in bytes
   - Why: Shows actual memory consumption by pods
   - Usage: Used to calculate both peak and average memory utilization

2. `kubernetes.memory.limits`
   - What: Memory limit set for the pod
   - Why: Helps understand resource constraints and utilization percentages
   - Usage: Used to calculate memory usage percentage (usage/limit)

### CPU Metrics
1. `kubernetes.cpu.usage.total`
   - What: CPU usage in nanocores
   - Why: Provides precise CPU utilization measurement
   - Usage: Used to identify active pods and calculate CPU utilization

2. `kubernetes.cpu.limits`
   - What: CPU limit set for the pod in cores
   - Why: Shows maximum CPU allocation allowed
   - Usage: Used to calculate CPU usage percentage

### Why These Metrics?
1. **Resource Planning**
   - Memory metrics help identify memory leaks and sizing requirements
   - CPU metrics help optimize pod scheduling and resource allocation

2. **Pod Lifecycle**
   - CPU usage helps distinguish between active and completed pods
   - Memory patterns help identify pod restart needs

3. **Capacity Planning**
   - Peak vs average usage helps determine optimal resource limits
   - Usage percentages help prevent resource contention

## Usage

Run the script to get an interactive session:
```bash
python pod_memory_metrics.py
```

The script will:
1. List available Kubernetes clusters
2. Let you choose time range (shortcuts: 1h, 6h, 24h, 7d)
3. Allow filtering by namespace and pod name patterns
4. Show comprehensive resource usage with:
   - Memory usage (peak and average)
   - CPU utilization (peak and average)
   - Resource usage percentages
   - Pod status (Active/Completed)

### Example API Usage

```python
from pod_memory_metrics import setup_datadog_client, get_pod_metrics

client = setup_datadog_client()
results = get_pod_metrics(
    client=client,
    cluster_name="your-cluster",    # Required: Cluster name
    namespace="your-namespace",     # Optional: Filter by namespace
    pod_name_filter="your-pod*",    # Optional: Filter by pod name pattern
    start_time=datetime(...),       # Optional: Start of time range
    end_time=datetime(...)         # Optional: End of time range
)
```

## Features

- Query pod metrics with flexible time ranges
- Filter by cluster, namespace, and pod name patterns
- Show both peak and average resource utilization
- Distinguish between active and completed pods
- Sort by various metrics (memory, CPU, name, namespace)
- Resource distribution summaries for capacity planning

## Security Notes

1. Never commit the `.env` file to version control
2. Rotate your keys periodically
3. Use the principle of least privilege - create specific keys for specific uses
4. Consider using environment variables or secrets management in production

## Maintenance

The script is designed to be maintainable and extensible:
1. Easy to add new metrics by updating the metrics dictionary
2. Configurable output formatting
3. Modular design for easy testing and modification
4. Well-documented code with clear function purposes
