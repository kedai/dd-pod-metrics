#!/usr/bin/env python3
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import csv
from io import StringIO

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v1.api.metrics_api import MetricsApi
from dotenv import load_dotenv

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

def parse_relative_time(time_str: str) -> Optional[timedelta]:
    """Parse relative time string (e.g., '5h', '7d', '30m')."""
    if not time_str:
        return None
        
    # Handle special case 'now'
    if time_str.lower() == 'now':
        return timedelta(0)

    try:
        # Extract number and unit
        match = re.match(r'^(\d+)([hdm])$', time_str.lower())
        if not match:
            return None

        amount = int(match.group(1))
        unit = match.group(2)

        if unit == 'h':
            return timedelta(hours=amount)
        elif unit == 'd':
            return timedelta(days=amount)
        elif unit == 'm':
            return timedelta(minutes=amount)

    except (ValueError, AttributeError):
        return None

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
    now = datetime.now()
    end_time = now  # Always use current time
    start_time = start_time or (now - timedelta(hours=1))

    # Validate time range
    max_history = timedelta(days=7)
    time_range = end_time - start_time
    if time_range >= max_history + timedelta(minutes=1):  # Allow exactly 7d
        raise ValueError(f"Time range too large. Maximum is 7 days (7d)")

    # Split long time ranges into 24h chunks
    time_chunks = []
    current_start = start_time
    while current_start < end_time:
        chunk_end = min(current_start + timedelta(hours=24), end_time)
        time_chunks.append((current_start, chunk_end))
        current_start = chunk_end

    print(f"Time range: {start_time.strftime('%Y-%m-%d %H:%M:%S')} to {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    if len(time_chunks) > 1:
        print(f"Splitting into {len(time_chunks)} chunks of 24h or less")

    # Debug output to verify query construction
    print(f"\nDebug: Example query with filters:")
    print(f"Query: max:kubernetes.memory.usage{{*}} by {{kube_cluster_name,kube_namespace,pod_name}}")

    all_results = {}
    for chunk_start, chunk_end in time_chunks:
        print(f"\nQuerying chunk: {chunk_start.strftime('%Y-%m-%d %H:%M:%S')} to {chunk_end.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Convert to UTC timestamps for Datadog API
        from_ts = int(chunk_start.timestamp())
        to_ts = int(chunk_end.timestamp())

        # Add buffer for metric collection delay
        if (now - chunk_end).total_seconds() < 300:  # If end time is within last 5 minutes
            to_ts += 300  # Add 5 minute buffer for metric collection delay

        # Build the tag filter
        tag_filter = "*"
        if cluster_name:
            tag_filter = f"kube_cluster_name:{cluster_name}"
        if namespace:
            tag_filter = f"{tag_filter},kube_namespace:{namespace}" if tag_filter != "*" else f"kube_namespace:{namespace}"
        if pod_name_filter:
            # Handle wildcard patterns in pod name filter
            pod_filter = pod_name_filter.replace('*', '.*')
            tag_filter = f"{tag_filter},pod_name:{pod_filter}" if tag_filter != "*" else f"pod_name:{pod_filter}"

        for metric_name, metric_base in {
            "memory_max": "kubernetes.memory.usage",
            "memory_avg": "kubernetes.memory.usage",
            "memory_limit": "kubernetes.memory.limits",
            "memory_request": "kubernetes.memory.requests",
            "cpu_max": "kubernetes.cpu.usage.total",
            "cpu_avg": "kubernetes.cpu.usage.total",
            "cpu_limit": "kubernetes.cpu.limits",
            "cpu_request": "kubernetes.cpu.requests"
        }.items():
            # Build the full query with proper aggregation
            agg = "max" if "max" in metric_name else "avg"
            query = f"{agg}:{metric_base}{{{tag_filter}}} by {{kube_cluster_name,kube_namespace,pod_name}}"
            
            print(f"Querying {metric_name}...")
            try:
                result = api_instance.query_metrics(
                    _from=from_ts,
                    to=to_ts,
                    query=query
                )
                
                # Merge results
                if metric_name not in all_results:
                    all_results[metric_name] = result.to_dict()
                else:
                    # Merge series data
                    existing_series = all_results[metric_name].get('series', [])
                    new_series = result.to_dict().get('series', [])
                    
                    # Create lookup for existing series
                    existing_lookup = {
                        (s.get('scope', ''), tuple(sorted(s.get('tag_set', [])))): s
                        for s in existing_series
                    }
                    
                    # Merge new points into existing series or add new series
                    for new_s in new_series:
                        key = (new_s.get('scope', ''), tuple(sorted(new_s.get('tag_set', []))))
                        if key in existing_lookup:
                            # Merge points
                            existing_points = existing_lookup[key].get('pointlist', [])
                            new_points = new_s.get('pointlist', [])
                            all_points = existing_points + new_points
                            # Sort by timestamp and remove duplicates
                            unique_points = list({p[0]: p for p in all_points}.values())
                            unique_points.sort(key=lambda x: x[0])
                            existing_lookup[key]['pointlist'] = unique_points
                        else:
                            existing_series.append(new_s)
                    
                    all_results[metric_name]['series'] = existing_series
                    
            except Exception as e:
                print(f"Warning: Failed to fetch {metric_name}: {str(e)}")

    return all_results

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
    print("\nEnter relative time range (e.g., 5h, 7d, 30m)")
    print("Examples:")
    print("  1h  = Last 1 hour")
    print("  12h = Last 12 hours")
    print("  1d  = Last 1 day")
    print("  7d  = Last 7 days (maximum)")
    print("  30m = Last 30 minutes")
    print("Press Enter to use default (last hour)")

    now = datetime.now()
    max_history = timedelta(days=7)

    while True:
        time_range = input("\nTime range (default: 1h): ").strip()
        if not time_range:
            time_range = "1h"

        delta = parse_relative_time(time_range)
        if not delta:
            print("Invalid time format. Use <number><unit> where unit is 'm' (minutes), 'h' (hours), or 'd' (days)")
            print("Example: 12h, 7d, 30m")
            continue

        if delta >= max_history + timedelta(minutes=1):  # Allow exactly 7d
            print(f"Time range too large. Maximum is 7 days (7d)")
            continue

        if delta.total_seconds() < 60:  # At least 1 minute
            print(f"Time range too small. Minimum is 1 minute (1m)")
            continue

        end_time = now
        start_time = end_time - delta

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

        # Ask about saving to file
        save_to_file = input("\nSave output to file? (y/N): ").strip().lower() == 'y'
        output_file = None
        if save_to_file:
            default_filename = f"pod_metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            output_file = input(f"Enter output filename (default: {default_filename}): ").strip() or default_filename

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
        if not results.get('memory_max', {}).get('series'):
            print("\nNo metrics found for the specified time range and filters.")
            print("This could be because:")
            print("1. No pods matched your filters")
            print("2. No metrics were collected in the specified time range")
            print("3. The time range is too far in the past (data retention limits)")
            return

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
            if memory_max is not None and memory_limit is not None and memory_limit > 0:
                memory_percent = (memory_max / memory_limit) * 100

            cpu_percent = None
            if cpu_max is not None and cpu_limit is not None and cpu_limit > 0:
                cpu_percent = (cpu_max / cpu_limit) * 100

            # Skip pods with no memory data or below threshold
            if memory_max is None or memory_max < threshold:
                continue

            # Add pod data
            pod_data.append({
                'cluster': cluster,
                'namespace': namespace,
                'pod': pod,
                'memory_max': memory_max,
                'memory_avg': memory_avg or 0,
                'memory_request': memory_request or 0,
                'memory_limit': memory_limit or 0,
                'memory_percent': memory_percent or 0,
                'cpu_max': cpu_max or 0,
                'cpu_avg': cpu_avg or 0,
                'cpu_request': cpu_request or 0,
                'cpu_limit': cpu_limit or 0,
                'cpu_percent': cpu_percent or 0,
                'base_name': get_base_pod_name(pod)
            })

        # Sort data based on user preference
        sort_key = None
        reverse = True

        if sort_option == "Memory (low to high)":
            sort_key = lambda x: x['memory_max']
            reverse = False
        elif sort_option == "CPU (high to low)":
            sort_key = lambda x: x['cpu_max'] if x['cpu_max'] is not None else 0
        elif sort_option == "CPU (low to high)":
            sort_key = lambda x: x['cpu_max'] if x['cpu_max'] is not None else float('inf')
            reverse = False
        elif sort_option == "Name":
            sort_key = lambda x: x['base_name']
        else:  # Namespace
            sort_key = lambda x: (x['namespace'], x['memory_max'])
            reverse = True

        pod_data.sort(key=sort_key, reverse=reverse)

        # Function to write output to console
        def write_console(line):
            print(line)

        # Open output file if requested
        output_fp = None
        csv_writer = None
        if output_file:
            output_fp = open(output_file, 'w', newline='')
            csv_writer = csv.writer(output_fp)
            
            # Write CSV metadata
            metadata = [
                ['Pod Resource Usage Report'],
                ['Generated at', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
                ['Time range', f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} to {end_time.strftime('%Y-%m-%d %H:%M:%S')}"],
                ['Cluster', cluster_name],
                ['Namespace filter', namespace or 'All'],
                ['Pod name filter', pod_filter or 'All'],
                ['Memory threshold (MB)', str(threshold)],
                ['Sort by', sort_option],
                [],  # Empty row for spacing
            ]
            csv_writer.writerows(metadata)

        try:
            # Console output header
            write_console("\nPod Resource Usage:")
            write_console("=" * 220)
            
            headers = [
                ("Namespace", 20),
                ("Pod", 60),
                ("Memory", 60),
                ("CPU", 60)
            ]

            # Print console header
            header_line = " | ".join(f"{name:<{width}}" for name, width in headers)
            write_console(header_line)

            # Print console subheader
            subheader = (
                f"{'':20} | "  # Namespace
                f"{'':60} | "  # Pod
                f"{'Max':15}{'Avg':15}{'Request':15}{'Limit':15} | "  # Memory columns
                f"{'Max':15}{'Avg':15}{'Request':15}{'Limit':15}"  # CPU columns
            )
            write_console(subheader)
            write_console("-" * 220)

            # Write CSV headers
            if csv_writer:
                csv_headers = [
                    'Namespace', 'Pod Name', 
                    'Memory Max (MB)', 'Memory Avg (MB)', 'Memory Request (MB)', 'Memory Limit (MB)', 'Memory Utilization (%)',
                    'CPU Max (cores)', 'CPU Avg (cores)', 'CPU Request (cores)', 'CPU Limit (cores)', 'CPU Utilization (%)'
                ]
                csv_writer.writerow(csv_headers)

            for pod in pod_data:
                # Get raw values for CSV
                memory_max = pod['memory_max']
                memory_avg = pod['memory_avg']
                memory_request = pod['memory_request']
                memory_limit = pod['memory_limit']
                cpu_max = pod['cpu_max']
                cpu_avg = pod['cpu_avg']
                cpu_request = pod['cpu_request']
                cpu_limit = pod['cpu_limit']
                
                # Calculate percentages
                memory_percent = (memory_max / memory_limit * 100) if memory_max is not None and memory_limit and memory_limit > 0 else None
                cpu_percent = (cpu_max / cpu_limit * 100) if cpu_max is not None and cpu_limit and cpu_limit > 0 else None

                # Format values for console display
                memory_max_str = format_memory_size(memory_max) if memory_max is not None else "N/A"
                memory_avg_str = format_memory_size(memory_avg) if memory_avg is not None else "N/A"
                memory_req_str = format_memory_size(memory_request) if memory_request is not None else "No request"
                memory_lim_str = format_memory_size(memory_limit) if memory_limit is not None else "No limit"

                cpu_max_str = format_cpu(cpu_max * 1e9) if cpu_max is not None else "N/A"
                cpu_avg_str = format_cpu(cpu_avg * 1e9) if cpu_avg is not None else "N/A"
                cpu_req_str = format_cpu(cpu_request * 1e9) if cpu_request is not None else "No request"
                cpu_lim_str = format_cpu(cpu_limit * 1e9) if cpu_limit is not None else "No limit"

                # Format console line
                console_line = (
                    f"{pod['namespace']:<20} | "
                    f"{pod['base_name']:<60} | "
                    f"{memory_max_str:<15}{memory_avg_str:<15}{memory_req_str:<15}{memory_lim_str:<15} | "
                    f"{cpu_max_str:<15}{cpu_avg_str:<15}{cpu_req_str:<15}{cpu_lim_str:<15}"
                )
                write_console(console_line)

                # Write CSV row with raw values
                if csv_writer:
                    csv_writer.writerow([
                        pod['namespace'],
                        pod['base_name'],
                        f"{memory_max:.1f}" if memory_max is not None else "",
                        f"{memory_avg:.1f}" if memory_avg is not None else "",
                        f"{memory_request:.1f}" if memory_request is not None else "",
                        f"{memory_limit:.1f}" if memory_limit is not None else "",
                        f"{memory_percent:.1f}" if memory_percent is not None else "",
                        f"{cpu_max:.3f}" if cpu_max is not None else "",
                        f"{cpu_avg:.3f}" if cpu_avg is not None else "",
                        f"{cpu_request:.3f}" if cpu_request is not None else "",
                        f"{cpu_limit:.3f}" if cpu_limit is not None else "",
                        f"{cpu_percent:.1f}" if cpu_percent is not None else ""
                    ])

            # Console summary
            write_console("=" * 220)
            write_console(f"Total pods shown: {len(pod_data)}")

            if not pod_data:
                write_console("\nNo pods found matching the criteria. This could be because:")
                write_console("1. No pods exceeded the memory threshold")
                write_console("2. No metrics were collected in the specified time range")
                write_console("3. The time range is too far in the past (data retention limits)")
            else:
                total_memory = sum(x['memory_max'] for x in pod_data)
                total_cpu = sum(x['cpu_max'] for x in pod_data if x['cpu_max'] is not None)
                write_console(f"Total memory usage: {format_memory_size(total_memory)}")
                write_console(f"Total CPU usage: {format_cpu(total_cpu * 1e9)}")

                # Write summary statistics
                if csv_writer:
                    csv_writer.writerows([
                        [],  # Empty row for spacing
                        ['Summary Statistics'],
                        ['Total Pods', len(pod_data)],
                        ['Total Memory Usage (MB)', f"{total_memory:.1f}"],
                        ['Total CPU Usage (cores)', f"{total_cpu:.3f}"]
                    ])

                    # Resource distribution
                    memory_values = [x['memory_max'] for x in pod_data]
                    csv_writer.writerows([
                        [],
                        ['Memory Distribution (MB)'],
                        ['Min', f"{min(memory_values):.1f}"],
                        ['Max', f"{max(memory_values):.1f}"],
                        ['Average', f"{sum(memory_values)/len(memory_values):.1f}"]
                    ])

                    cpu_values = [x['cpu_max'] for x in pod_data if x['cpu_max'] is not None]
                    if cpu_values:
                        csv_writer.writerows([
                            [],
                            ['CPU Distribution (cores)'],
                            ['Min', f"{min(cpu_values):.3f}"],
                            ['Max', f"{max(cpu_values):.3f}"],
                            ['Average', f"{sum(cpu_values)/len(cpu_values):.3f}"]
                        ])

            if output_file:
                write_console(f"\nData saved to: {output_file}")

        finally:
            if output_fp:
                output_fp.close()

    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    main()
