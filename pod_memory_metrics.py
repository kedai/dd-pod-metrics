#!/usr/bin/env python3
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v1.api.metrics_api import MetricsApi
from dotenv import load_dotenv

# Configuration constants
ACTIVE_POD_WINDOW_SECONDS = 60  # Time window to consider a pod active (1 minute)

def setup_datadog_client() -> ApiClient:
    """Initialize Datadog API client with credentials."""
    load_dotenv()

    # Ensure required environment variables are set
    required_vars = ['DD_API_KEY_ID', 'DD_API_KEY', 'DD_APP_KEY']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

    configuration = Configuration()
    configuration.api_key['apiKeyAuth'] = os.getenv('DD_API_KEY')
    configuration.api_key['appKeyAuth'] = os.getenv('DD_APP_KEY')
    return ApiClient(configuration)

def parse_datetime(dt_str: str) -> Optional[datetime]:
    """Parse datetime string in various formats."""
    formats = [
        "%Y-%m-%d %H:%M",  # 2025-03-06 11:30
        "%Y-%m-%d",        # 2025-03-06
        "%H:%M",          # 11:30 (today)
        "%m-%d",          # 03-06 (this year)
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(dt_str, fmt)
            now = datetime.now()

            # Handle relative formats
            if fmt == "%H:%M":
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
            elif fmt == "%m-%d":
                dt = dt.replace(year=now.year)

            return dt
        except ValueError:
            continue

    return None

def get_pod_metrics(
    client: ApiClient,
    cluster_name: str = None,
    namespace: str = None,
    pod_name_filter: str = None,
    start_time: datetime = None,
    end_time: datetime = None
) -> Dict:
    """
    Query Datadog for Kubernetes pod metrics.
    """
    print("\nQuerying Datadog for pod metrics...")
    api_instance = MetricsApi(client)

    # Set up time range
    end_time = end_time or datetime.now()
    start_time = start_time or (end_time - timedelta(hours=1))

    # Build query string with tags
    tags = []
    if cluster_name:
        tags.append(f"kube_cluster_name:{cluster_name}")
    if namespace:
        tags.append(f"kube_namespace:{namespace}")
    if pod_name_filter:
        # Simple tag filtering - let Datadog handle the wildcards
        tags.append(f"pod_name:{pod_name_filter}")

    # Base tag filter with required tags
    tag_filter = "{" + ",".join(tags) + "}" if tags else "{*}"

    # Query both memory and CPU metrics with max and avg
    metrics = {
        "memory_max": f"max:kubernetes.memory.usage{tag_filter} by {{kube_cluster_name,kube_namespace,pod_name}}",
        "memory_avg": f"avg:kubernetes.memory.usage{tag_filter} by {{kube_cluster_name,kube_namespace,pod_name}}",
        "memory_limit": f"max:kubernetes.memory.limits{tag_filter} by {{kube_cluster_name,kube_namespace,pod_name}}",
        "memory_request": f"max:kubernetes.memory.requests{tag_filter} by {{kube_cluster_name,kube_namespace,pod_name}}",
        "cpu_max": f"max:kubernetes.cpu.usage.total{tag_filter} by {{kube_cluster_name,kube_namespace,pod_name}}",
        "cpu_avg": f"avg:kubernetes.cpu.usage.total{tag_filter} by {{kube_cluster_name,kube_namespace,pod_name}}",
        "cpu_limit": f"max:kubernetes.cpu.limits{tag_filter} by {{kube_cluster_name,kube_namespace,pod_name}}",
        "cpu_request": f"max:kubernetes.cpu.requests{tag_filter} by {{kube_cluster_name,kube_namespace,pod_name}}"
    }

    print(f"Time range: {start_time.strftime('%Y-%m-%d %H:%M:%S')} to {end_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Debug output to verify query construction
    print(f"\nDebug: Example query with filters:")
    print(f"Query: {metrics['memory_max']}")

    results = {}
    for metric_name, query in metrics.items():
        print(f"Querying {metric_name}...")
        try:
            result = api_instance.query_metrics(
                _from=int(start_time.timestamp()),
                to=int(end_time.timestamp()),
                query=query
            )
            results[metric_name] = result.to_dict()
        except Exception as e:
            print(f"Warning: Failed to fetch {metric_name}: {str(e)}")

    return results

def format_memory_size(size_mb: float) -> str:
    """Format memory size to a human-readable format."""
    if size_mb >= 1024:
        return f"{size_mb/1024:.1f} GB"
    return f"{size_mb:.1f} MB"

def format_cpu(cpu: float) -> str:
    """Format CPU usage."""
    # Convert from nanoseconds to cores
    cores = cpu / 1e9
    if cores >= 1:
        return f"{cores:.2f} cores"
    return f"{cores*1000:.0f} mcores"

def get_user_input(prompt: str, valid_options: List[str]) -> str:
    """Get user input with validation."""
    while True:
        print(f"\n{prompt}")
        for i, option in enumerate(valid_options, 1):
            print(f"{i}. {option}")

        try:
            choice = input("\nEnter your choice (number): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(valid_options):
                return valid_options[idx]
            print("Invalid choice. Please try again.")
        except ValueError:
            print("Please enter a number.")

def get_sort_option() -> str:
    """Get sorting preference from user."""
    options = ["Memory (high to low)", "Memory (low to high)", "CPU (high to low)", "CPU (low to high)", "Name", "Namespace"]
    choice = get_user_input("Sort results by:", options)
    return choice

def get_datetime_range() -> Tuple[datetime, datetime]:
    """Get datetime range from user."""
    print("\nEnter time range (formats: YYYY-MM-DD HH:MM, YYYY-MM-DD, HH:MM, MM-DD)")
    print("Press Enter to use defaults (last hour)")

    while True:
        start_str = input("\nStart time (default: 1 hour ago): ").strip()
        if not start_str:
            return datetime.now() - timedelta(hours=1), datetime.now()

        end_str = input("End time (default: now): ").strip()
        if not end_str:
            end_time = datetime.now()
        else:
            end_time = parse_datetime(end_str)
            if not end_time:
                print("Invalid end time format")
                continue

        start_time = parse_datetime(start_str)
        if not start_time:
            print("Invalid start time format")
            continue

        if start_time >= end_time:
            print("Start time must be before end time")
            continue

        return start_time, end_time

def list_clusters(client: ApiClient) -> List[str]:
    """List available Kubernetes clusters."""
    api_instance = MetricsApi(client)
    try:
        # Query cluster tag values
        query = "avg:kubernetes.cpu.usage.total{*} by {kube_cluster_name}"
        end = int(datetime.now().timestamp())
        start = end - 3600  # Last hour

        result = api_instance.query_metrics(
            _from=start,
            to=end,
            query=query
        )

        # Extract unique cluster names
        clusters = set()
        if result.series:
            for series in result.series:
                for tag in series.tag_set:
                    if tag.startswith('kube_cluster_name:'):
                        cluster_name = tag.split(':', 1)[1]
                        if cluster_name and cluster_name.lower() != 'n/a':
                            clusters.add(cluster_name)

        return sorted(list(clusters))
    except Exception as e:
        print(f"Warning: Failed to fetch clusters: {str(e)}")
        return []

def get_base_pod_name(pod_name: str) -> str:
    """Extract base pod name without the random suffix."""
    # Common patterns for pod name suffixes, ordered from most specific to least
    patterns = [
        r'-[0-9a-f]{8,10}-[0-9a-z]{5,7}$',  # kubernetes style: -7d6cf8d579-x2nds
        r'-[0-9a-f]{8,16}$',                 # hash suffix: -7d6cf8d579
        r'-[0-9]+$',                         # numbered: -1, -2, etc
        r'-[a-z0-9]{5,10}$'                 # other random suffixes
    ]

    base_name = pod_name
    for pattern in patterns:
        match = re.search(pattern, base_name)
        if match:
            base_name = base_name[:match.start()]
            break

    return base_name

def main():
    """Main function to demonstrate usage."""
    try:
        client = setup_datadog_client()

        # First, list available clusters
        print("Fetching available Kubernetes clusters...")
        clusters = list_clusters(client)
        if not clusters:
            print("No Kubernetes clusters found in the last hour")
            return

        # Let user choose the cluster
        cluster_name = get_user_input(
            "Choose a cluster to query:",
            clusters
        )

        # Get time range
        start_time, end_time = get_datetime_range()

        # Get memory threshold filter
        while True:
            try:
                threshold = input("\nEnter minimum memory threshold in MB (press Enter for no filter): ").strip()
                if not threshold:
                    threshold = 0
                    break
                threshold = float(threshold)
                if threshold >= 0:
                    break
                print("Please enter a positive number.")
            except ValueError:
                print("Please enter a valid number.")

        # Get namespace filter
        namespace = input("\nEnter namespace to filter (press Enter for all namespaces): ").strip() or None

        # Get pod name filter with better guidance
        print("\nEnter pod name pattern to filter. Examples:")
        print("  podname-*            : Match all pods starting with 'podname-'")
        print("  *some-suffix*  : Match pods containing 'some-suffix'")
        print("  podname-xxx      : Match exact name 'podname-xxx'")
        pod_filter = input("Pod name pattern (press Enter for all pods): ").strip() or None

        # Get sort preference
        sort_option = get_sort_option()

        print(f"\nFetching metrics for cluster: {cluster_name}")
        if namespace:
            print(f"Namespace filter: {namespace}")
        if pod_filter:
            print(f"Pod name filter: {pod_filter}")
        if threshold:
            print(f"Memory threshold: {threshold} MB")

        results = get_pod_metrics(
            client=client,
            cluster_name=cluster_name,
            namespace=namespace,
            pod_name_filter=pod_filter,
            start_time=start_time,
            end_time=end_time
        )

        # Process and print results
        if results.get('memory_max', {}).get('series'):
            # Collect all pod data
            pod_data = []
            now = datetime.now()

            # Process memory metrics (max and avg)
            memory_max_data = {
                (series.get('scope', 'unknown'), tuple(series.get('tag_set', []))): series.get('pointlist', [])
                for series in results.get('memory_max', {}).get('series', [])
            }
            memory_avg_data = {
                (series.get('scope', 'unknown'), tuple(series.get('tag_set', []))): series.get('pointlist', [])
                for series in results.get('memory_avg', {}).get('series', [])
            }

            # Process memory limits
            memory_limit_data = {
                (series.get('scope', 'unknown'), tuple(series.get('tag_set', []))): series.get('pointlist', [])
                for series in results.get('memory_limit', {}).get('series', [])
            }

            # Process memory requests
            memory_request_data = {
                (series.get('scope', 'unknown'), tuple(series.get('tag_set', []))): series.get('pointlist', [])
                for series in results.get('memory_request', {}).get('series', [])
            }

            # Process CPU metrics (max and avg)
            cpu_max_data = {
                (series.get('scope', 'unknown'), tuple(series.get('tag_set', []))): series.get('pointlist', [])
                for series in results.get('cpu_max', {}).get('series', [])
            }
            cpu_avg_data = {
                (series.get('scope', 'unknown'), tuple(series.get('tag_set', []))): series.get('pointlist', [])
                for series in results.get('cpu_avg', {}).get('series', [])
            }

            # Process CPU limits
            cpu_limit_data = {
                (series.get('scope', 'unknown'), tuple(series.get('tag_set', []))): series.get('pointlist', [])
                for series in results.get('cpu_limit', {}).get('series', [])
            }

            # Process CPU requests
            cpu_request_data = {
                (series.get('scope', 'unknown'), tuple(series.get('tag_set', []))): series.get('pointlist', [])
                for series in results.get('cpu_request', {}).get('series', [])
            }

            for (scope, tag_set), points in memory_max_data.items():
                if not points:
                    continue

                # Extract tags
                tags = {
                    tag.split(':', 1)[0]: tag.split(':', 1)[1]
                    for tag in tag_set
                    if ':' in tag
                }

                cluster = tags.get('kube_cluster_name', 'unknown')
                namespace = tags.get('kube_namespace', 'unknown')
                pod = tags.get('pod_name', 'unknown')

                # Get memory metrics
                memory_max = None
                memory_avg = None
                if (scope, tag_set) in memory_max_data:
                    for point in reversed(memory_max_data[(scope, tag_set)]):
                        if point[1] is not None:
                            memory_max = point[1] / (1024 * 1024)  # Convert to MB
                            break
                if (scope, tag_set) in memory_avg_data:
                    for point in reversed(memory_avg_data[(scope, tag_set)]):
                        if point[1] is not None:
                            memory_avg = point[1] / (1024 * 1024)  # Convert to MB
                            break

                # Get memory request
                memory_request = None
                if (scope, tag_set) in memory_request_data:
                    for point in reversed(memory_request_data[(scope, tag_set)]):
                        if point[1] is not None:
                            memory_request = point[1] / (1024 * 1024)  # Convert to MB
                            break

                # Get memory limit
                memory_limit = None
                if (scope, tag_set) in memory_limit_data:
                    for point in reversed(memory_limit_data[(scope, tag_set)]):
                        if point[1] is not None:
                            memory_limit = point[1] / (1024 * 1024)  # Convert to MB
                            break

                # Get the latest timestamp and CPU value
                latest_cpu_timestamp = None
                cpu_max = None
                cpu_avg = None
                if (scope, tag_set) in cpu_max_data:
                    for point in reversed(cpu_max_data[(scope, tag_set)]):
                        if point[1] is not None:
                            # Datadog timestamps are in seconds, need to convert from ms to s
                            latest_cpu_timestamp = datetime.fromtimestamp(point[0] / 1000.0)
                            cpu_max = point[1] / 1e9  # Convert nanoseconds to cores
                            break
                if (scope, tag_set) in cpu_avg_data:
                    for point in reversed(cpu_avg_data[(scope, tag_set)]):
                        if point[1] is not None:
                            cpu_avg = point[1] / 1e9  # Convert nanoseconds to cores
                            break

                # Get CPU request
                cpu_request = None
                if (scope, tag_set) in cpu_request_data:
                    for point in reversed(cpu_request_data[(scope, tag_set)]):
                        if point[1] is not None:
                            cpu_request = point[1]  # Already in cores
                            break

                # Get CPU limit
                cpu_limit = None
                if (scope, tag_set) in cpu_limit_data:
                    for point in reversed(cpu_limit_data[(scope, tag_set)]):
                        if point[1] is not None:
                            cpu_limit = point[1]  # Already in cores
                            break

                # Calculate resource percentages
                memory_percent = None
                if memory_limit is not None and memory_limit > 0:
                    memory_percent = (memory_max / memory_limit) * 100

                cpu_percent = None
                if cpu_max is not None and cpu_limit is not None and cpu_limit > 0:
                    cpu_percent = (cpu_max / cpu_limit) * 100

                # Determine pod status based on recent CPU activity
                # Consider a pod completed if no CPU activity within the configured window
                status = "Completed"
                if latest_cpu_timestamp:
                    time_since_last_cpu = datetime.now() - latest_cpu_timestamp
                    if time_since_last_cpu.total_seconds() < ACTIVE_POD_WINDOW_SECONDS:
                        status = "Active"

                pod_data.append((
                    cluster, namespace, pod,
                    memory_max, memory_avg, memory_request, memory_limit, memory_percent,
                    cpu_max, cpu_avg, cpu_request, cpu_limit, cpu_percent,
                    status
                ))

            # Sort data based on user preference
            sort_key = None
            reverse = True

            if sort_option == "Memory (low to high)":
                sort_key = lambda x: x[3]
                reverse = False
            elif sort_option == "CPU (high to low)":
                sort_key = lambda x: x[7] if x[7] is not None else 0
            elif sort_option == "CPU (low to high)":
                sort_key = lambda x: x[7] if x[7] is not None else float('inf')
                reverse = False
            elif sort_option == "Name":
                sort_key = lambda x: x[2]
            else:  # Namespace
                sort_key = lambda x: (x[1], x[3])
                reverse = True

            pod_data.sort(key=sort_key, reverse=reverse)

            print("\nPod Resource Usage (including completed pods):")
            print("=" * 240)
            headers = [
                ("Namespace", 20),
                ("Pod", 60),
                ("Memory", 60),
                ("CPU", 60),
                ("Status", 10)
            ]

            # Print header
            header_line = " | ".join(f"{name:<{width}}" for name, width in headers)
            print(header_line)

            # Print subheader for Memory and CPU
            subheader = (
                f"{'':20} | "  # Namespace
                f"{'':60} | "  # Pod
                f"{'Max':15}{'Avg':15}{'Request':15}{'Limit':15} | "  # Memory columns
                f"{'Max':15}{'Avg':15}{'Request':15}{'Limit':15} | "  # CPU columns
                f"{'':10}"  # Status
            )
            print(subheader)
            print("-" * 240)

            active_pods = 0
            completed_pods = 0

            for _, namespace, pod, memory_max, memory_avg, memory_req, memory_lim, memory_pct, cpu_max, cpu_avg, cpu_req, cpu_lim, cpu_pct, status in pod_data:
                base_pod = get_base_pod_name(pod)

                # Format memory values
                memory_max_str = format_memory_size(memory_max) if memory_max is not None else "N/A"
                memory_avg_str = format_memory_size(memory_avg) if memory_avg is not None else "N/A"
                memory_req_str = format_memory_size(memory_req) if memory_req is not None else "No request"
                memory_lim_str = format_memory_size(memory_lim) if memory_lim is not None else "No limit"

                # Format CPU values
                cpu_max_str = format_cpu(cpu_max * 1e9) if cpu_max is not None else "N/A"
                cpu_avg_str = format_cpu(cpu_avg * 1e9) if cpu_avg is not None else "N/A"
                cpu_req_str = format_cpu(cpu_req * 1e9) if cpu_req is not None else "No request"
                cpu_lim_str = format_cpu(cpu_lim * 1e9) if cpu_lim is not None else "No limit"

                # Format line
                line = (
                    f"{namespace:<20} | "
                    f"{base_pod:<60} | "
                    f"{memory_max_str:<15}{memory_avg_str:<15}{memory_req_str:<15}{memory_lim_str:<15} | "
                    f"{cpu_max_str:<15}{cpu_avg_str:<15}{cpu_req_str:<15}{cpu_lim_str:<15} | "
                    f"{status:<10}"
                )
                print(line)

                if status == "Active":
                    active_pods += 1
                else:
                    completed_pods += 1

            print("=" * 240)
            print(f"Total pods shown: {len(pod_data)} ({active_pods} active, {completed_pods} completed)")
            total_memory = sum(x[3] for x in pod_data)
            total_cpu = sum(x[7] for x in pod_data if x[7] is not None)
            print(f"Total memory usage: {format_memory_size(total_memory)}")
            print(f"Total CPU usage: {format_cpu(total_cpu * 1e9)}")

            print("\nResource Distribution:")
            print("Memory (all pods):")
            memory_values = [x[3] for x in pod_data]
            print(f"  Min: {format_memory_size(min(memory_values))}")
            print(f"  Max: {format_memory_size(max(memory_values))}")
            print(f"  Average: {format_memory_size(sum(memory_values)/len(memory_values))}")

            cpu_values = [x[7] for x in pod_data if x[7] is not None]
            if cpu_values:
                print("CPU (active pods only):")
                print(f"  Min: {format_cpu(min(cpu_values) * 1e9)}")
                print(f"  Max: {format_cpu(max(cpu_values) * 1e9)}")
                print(f"  Average: {format_cpu(sum(cpu_values)/len(cpu_values) * 1e9)}")
        else:
            print("\nNo metrics data found for the specified filters")

    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    main()
