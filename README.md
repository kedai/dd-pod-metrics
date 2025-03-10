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
- **Choice**: Using CPU activity within a configurable time window (default: 1 minute)
- **Why**: Most reliable way to detect truly active pods, closely matching `kubectl top pods`
- **Trade-off**: Balances real-time accuracy with metric collection delays
- **Configuration**: Adjust `ACTIVE_POD_WINDOW_SECONDS` for different monitoring needs:
  ```python
  # For tighter real-time monitoring (default)
  ACTIVE_POD_WINDOW_SECONDS = 60  # 1 minute
  # For more lenient monitoring
  ACTIVE_POD_WINDOW_SECONDS = 300  # 5 minutes
  ```

#### Pod Status Criteria
1. **Active Pods**:
   - Must have CPU metrics in Datadog
   - Must have CPU activity within the configured window
   - Typically includes infrastructure pods (webserver, scheduler, triggerer)

2. **Completed Pods**:
   - No CPU metrics available, or
   - No CPU activity within the configured window
   - Typically includes task pods that have finished running

#### Window Size Trade-offs
- **Shorter Window (e.g., 1 minute)**:
  - More accurate real-time state
  - Better alignment with `kubectl top pods`
  - May miss pods if metric collection is delayed

- **Longer Window (e.g., 5 minutes)**:
  - Better handles metric collection delays
  - Shows more historical activity
  - May show completed pods as active longer

The default 1-minute window was chosen because:
1. Datadog's metric collection is typically < 1 minute
2. Provides best alignment with `kubectl top pods`
3. Accurately captures infrastructure pod status
4. Minimizes false positives from completed task pods

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

### Understanding Metric Collection Delays

When querying Datadog for pod metrics, there can be delays between:
1. When a pod is actually running in Kubernetes (what `kubectl top pods` shows)
2. When those metrics appear in Datadog's API

These delays occur due to:
- Time needed for the Datadog agent to collect metrics
- Processing and sending metrics to Datadog's servers
- API availability delays

The pod status detection window (`ACTIVE_POD_WINDOW_SECONDS`) helps handle these delays:
- Default 1-minute window works well for most cases
- Increase the window if you notice pods being marked as completed too early
- Decrease the window if you need tighter real-time monitoring

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
   - Pod status (Active/Completed) based on recent CPU activity

### Configuration

The script's behavior can be customized through configuration constants:

```python
# At the top of pod_memory_metrics.py

# Time window to consider a pod active (default: 1 minute)
# Decrease for tighter real-time monitoring
# Increase if experiencing metric collection delays
ACTIVE_POD_WINDOW_SECONDS = 60
```

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

### Important Configuration Notes

1. **Pod Status Detection Window**:
   ```python
   # Adjust this based on your task duration patterns
   ACTIVE_POD_WINDOW_SECONDS = 60  # Default: 1 minute
   ```
   This ensures accurate:
   - Task duration measurement
   - Resource utilization patterns
   - Completion status detection

2. **Best Practices**:
   - Start with shorter windows for more accurate real-time data
   - Increase window size if tasks are being marked complete too early
   - Consider metric collection delays in your environment
   - Monitor both active and completed pod metrics

#### Resource Behavior
1. **Memory**
   - Exceeding limit → Pod OOMKilled
   - No limit → Can use all node memory (bad for other pods and more nodes required)
   - Request → Scheduling minimum

2. **CPU**
   - Exceeding limit → Throttled (not killed)
   - No limit → Can use all available CPU
   - Request → Guaranteed minimum

