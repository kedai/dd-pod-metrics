#!/usr/bin/env python3
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import re
import argparse
import math

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v1.api.metrics_api import MetricsApi
from datadog_api_client.v1.api.events_api import EventsApi
from tabulate import tabulate
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
    configuration.server_variables['site'] = os.getenv('DD_SITE', 'datadoghq.com')
    return ApiClient(configuration)

def format_memory_size(size_bytes: float) -> str:
    """Format memory size in human readable format"""
    if size_bytes == 0:
        return "0 B"

    units = ['B', 'KB', 'MB', 'GB', 'TB']
    size = float(size_bytes)
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    return f"{size:.1f} {units[unit_index]}"

class PodDiagnostics:
    def __init__(self):
        self.api_client = setup_datadog_client()
        self.metrics_api = MetricsApi(self.api_client)
        self.events_api = EventsApi(self.api_client)

    def format_cpu(self, nanocores: float) -> str:
        """Format CPU usage from nanocores to a human-readable string"""
        if nanocores == 0:
            return "0m"

        # Convert nanocores to millicores (1m = 1/1000 CPU)
        millicores = nanocores / 1_000_000  # 1 CPU = 1000m = 1B nanocores

        if millicores >= 1000:
            return f"{millicores/1000:.1f}"  # Show as whole CPUs if >= 1000m
        else:
            return f"{millicores:.1f}m"  # Show as millicores if < 1000m

    def get_pod_metrics(self, cluster: str, namespace: str, pod_name: str, hours: int = 24, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> Dict:
        """Get detailed pod metrics including memory, CPU, and network"""
        metrics = {}

        # Set up time window
        end = end_time if end_time else datetime.now()
        start = start_time if start_time else (end - timedelta(hours=hours))

        print(f"\nGathering metrics for pod {pod_name} over the specified time window...")
        print(f"Time range: {start} to {end}\n")
        print("Starting metric collection...\n")

        try:
            # Get pod status phase from kube state metrics
            print("Querying pod status...")
            status_metrics = self._query_single_metric(
                'kubernetes_state.pod.status_phase',
                'pod_status',
                cluster,
                namespace,
                pod_name,
                start,
                end
            )
            if status_metrics:
                metrics['pod_status'] = status_metrics
            print("Pod status metrics collected\n")

            # Get memory metrics
            metrics['memory'] = self._query_single_metric('kubernetes.memory.usage', 'memory', cluster, namespace, pod_name, start, end)
            print("Memory metrics collected")

            metrics['memory_limit'] = self._query_single_metric('kubernetes.memory.limits', 'memory limit', cluster, namespace, pod_name, start, end)
            print("Memory limit metrics collected")

            metrics['memory_request'] = self._query_single_metric('kubernetes.memory.requests', 'memory request', cluster, namespace, pod_name, start, end)
            print("Memory request metrics collected")

            # Try multiple CPU metrics
            metrics['cpu'] = self._query_single_metric(
                'kubernetes.cpu.usage.total',
                'CPU',
                fallback_metrics=[
                    'kubernetes.cpu.usage',
                    'kubernetes.cpu.usage.system',
                    'kubernetes.cpu.usage.user'
                ],
                cluster=cluster,
                namespace=namespace,
                pod_name=pod_name,
                start=start,
                end=end
            )
            print("CPU metrics collected")

            metrics['cpu_limit'] = self._query_single_metric('kubernetes.cpu.limits', 'CPU limit', cluster, namespace, pod_name, start, end)
            print("CPU limit metrics collected")

            metrics['cpu_request'] = self._query_single_metric('kubernetes.cpu.requests', 'CPU request', cluster, namespace, pod_name, start, end)
            print("CPU request metrics collected")

        except Exception as e:
            print(f"Error querying metrics: {str(e)}")

        return metrics

    def _query_single_metric(self, metric_name: str, metric_type: str, cluster: str, namespace: str, pod_name: str, start: datetime, end: datetime, fallback_metrics: List[str] = None) -> Optional[Dict]:
        """Query a single metric and return processed values"""
        try:
            # Build query with specific tags
            query = f"avg:{metric_name}{{kube_cluster_name:{cluster},kube_namespace:{namespace},pod_name:{pod_name}}}"
            # print(f"Debug: Query: {query}")

            response = self.metrics_api.query_metrics(
                _from=int(start.timestamp()),
                to=int(end.timestamp()),
                query=query
            )

            if response and hasattr(response, 'series') and response.series:
                # print(f"Debug: Found {len(response.series)} series")
                series = response.series[0]

                # Debug series metadata
                # if hasattr(series, 'metric'):
                #     print(f"Debug: Metric name: {series.metric}")
                # if hasattr(series, 'scope'):
                #     print(f"Debug: Series scope: {series.scope}")
                # if hasattr(series, 'unit'):
                #     print(f"Debug: Series unit: {series.unit}")

                points = series.pointlist
                if points:
                    # print(f"Debug: Found {len(points)} points")
                    # if points[0]._data_store and 'value' in points[0]._data_store:
                    #     print(f"Debug: Sample point value: {points[0]._data_store['value']}")

                    # Process all points directly
                    result = self._process_points(points)
                    # print(f"Debug: Processed values: {result}")
                    return result
                else:
                    print(f"Warning: No {metric_type} data points in series")
            else:
                print(f"Warning: No {metric_type} series found")
                
            # Try fallback metrics if provided and primary metric failed
            if fallback_metrics:
                for fallback_metric in fallback_metrics:
                    print(f"Trying fallback metric: {fallback_metric}")
                    try:
                        query = f"avg:{fallback_metric}{{kube_cluster_name:{cluster},kube_namespace:{namespace},pod_name:{pod_name}}}"
                        response = self.metrics_api.query_metrics(
                            _from=int(start.timestamp()),
                            to=int(end.timestamp()),
                            query=query
                        )
                        
                        if response and hasattr(response, 'series') and response.series:
                            series = response.series[0]
                            points = series.pointlist
                            if points:
                                result = self._process_points(points)
                                print(f"Found data using fallback metric: {fallback_metric}")
                                return result
                    except Exception as e:
                        print(f"Error with fallback metric {fallback_metric}: {str(e)}")
                        continue

        except Exception as e:
            print(f"Error querying {metric_type}: {str(e)}")
            print(f"Debug: Exception type: {type(e)}")
            if hasattr(e, '__dict__'):
                print(f"Debug: Exception details: {e.__dict__}")

        return None

    def get_pod_events(self, cluster: str, namespace: str, pod_name: str, hours: int = 24, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> List[Dict]:
        """Get recent events for the pod"""
        if start_time and end_time:
            start = start_time
            end = end_time
        else:
            now = datetime.now()
            start = now - timedelta(hours=hours)
            end = now

        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())

        # Build minimal tag set for wider event capture
        tags = [f"kube_cluster_name:{cluster}"]
        if namespace:
            tags.append(f"kube_namespace:{namespace}")

        try:
            tags_str = ",".join(tags)
            response = self.events_api.list_events(
                start=start_ts,
                end=end_ts,
                tags=tags_str,
                unaggregated=True,
                priority="normal",
                page=0  # Changed from page_size=1000 to page=0 to get first page of results
            )

            if not hasattr(response, 'events'):
                return []

            # Filter events related to our pod, checking all possible fields
            pod_events = []
            for event in response.events:
                # Safely get event attributes with fallbacks to empty string
                event_attrs = [
                    getattr(event, 'title', ''),
                    getattr(event, 'text', ''),
                    getattr(event, 'alert_type', ''),
                    getattr(event, 'source_type_name', ''),
                    # Safely handle tags
                    *(getattr(event, 'tags', []) or [])
                ]

                # Filter out None values and convert to lowercase strings
                event_text = " ".join(str(attr).lower() for attr in event_attrs if attr is not None)

                # Check if this event is specifically about our pod
                is_pod_event = False

                # First check pod name in tags
                pod_tags = [tag for tag in getattr(event, 'tags', []) or []
                          if tag.startswith('pod_name:') or tag.startswith('kube_pod:')]
                if any(pod_name in tag for tag in pod_tags):
                    is_pod_event = True

                # Then check title and text
                if not is_pod_event:
                    title = getattr(event, 'title', '').lower()
                    text = getattr(event, 'text', '').lower()

                    # Only match if pod name is explicitly mentioned
                    is_pod_event = (
                        f"pod {pod_name.lower()}" in title or
                        f"pod {pod_name.lower()}" in text or
                        f"pod/{pod_name.lower()}" in title or
                        f"pod/{pod_name.lower()}" in text
                    )

                if is_pod_event:
                    pod_events.append(event)

            return pod_events

        except Exception as e:
            print(f"Error querying events: {e}")
            if hasattr(e, 'body'):
                print(f"Error body: {e.body}")
            return []

    def find_matching_pods(self, cluster: str, namespace: str, pod_pattern: str, hours: int = 24, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> List[Dict]:
        """Find pods matching a pattern in the given namespace"""
        if start_time and end_time:
            start = start_time
            end = end_time
        else:
            now = datetime.now()
            start = now - timedelta(hours=hours)
            end = now

        # Build query to get pod names and timestamps
        tags = []
        if cluster:
            tags.append(f"kube_cluster_name:{cluster}")
        if namespace:
            tags.append(f"kube_namespace:{namespace}")

        query = f"max:kubernetes.memory.usage{{{','.join(tags)}}} by {{pod_name}}"

        try:
            response = self.metrics_api.query_metrics(
                _from=int(start.timestamp()),
                to=int(end.timestamp()),
                query=query
            )

            matching_pods = []
            if response and hasattr(response, 'series'):
                for series in response.series:
                    if hasattr(series, 'to_dict'):
                        series_dict = series.to_dict()
                        if isinstance(series_dict, dict):
                            scope = series_dict.get('scope', '')
                            pointlist = series_dict.get('pointlist', [])

                            pod_name = None
                            for tag in scope.split(','):
                                if ':' in tag:
                                    key, value = tag.split(':', 1)
                                    if key == 'pod_name' and pod_pattern.lower() in value.lower():
                                        pod_name = value
                                        break

                            if pod_name and pointlist:
                                # Get the latest timestamp for the pod
                                latest_ts = max(point[0] for point in pointlist if point)
                                # Only include pods that fall within our time window
                                pod_time = datetime.fromtimestamp(latest_ts/1000)
                                if start <= pod_time <= end:
                                    matching_pods.append({
                                        'name': pod_name,
                                        'timestamp': latest_ts,
                                        'age_minutes': int((datetime.now() - pod_time).total_seconds() / 60)
                                    })

            # Sort by timestamp (newest first)
            return sorted(matching_pods, key=lambda x: x['timestamp'], reverse=True)
        except Exception as e:
            print(f"Warning: Failed to search for pods: {str(e)}")
            return []

    def select_pod(self, cluster: str, namespace: str, pod_pattern: str, hours: int = 24, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> Optional[str]:
        """Interactive pod selection based on pattern matching"""
        while True:
            matching_pods = self.find_matching_pods(cluster, namespace, pod_pattern, hours, start_time, end_time)

            if not matching_pods:
                print(f"\nNo pods found matching '{pod_pattern}' in the specified time window")
                time_window = f"{start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')}" if start_time and end_time else f"last {hours} hours"
                print(f"Time window: {time_window}")
                new_pattern = input("Enter a new search pattern (or 'q' to quit): ")
                if new_pattern.lower() == 'q':
                    return None
                pod_pattern = new_pattern
                continue

            print(f"\nFound {len(matching_pods)} matching pods in specified time window (showing newest first):")
            for i, pod in enumerate(matching_pods, 1):
                age = pod.get('age_minutes', 0)
                if age < 60:
                    age_str = f"{age}m"
                else:
                    age_str = f"{age//60}h {age%60}m"
                print(f"{i}. {pod['name']} (last seen {age_str} ago)")

            print("\nOptions:")
            print("- Enter a number to select that pod")
            print("- Enter a new search pattern to refine")
            print("- Enter 't=<time>' to change time window:")
            print("  Examples: 't=24' or 't=24h' for hours")
            print("           't=7d' for days")
            print("           't=1w' for weeks")
            print("- Enter 'q' to quit")

            choice = input("\nYour choice: ")

            if choice.lower() == 'q':
                return None
            elif choice.startswith('t='):
                delta = parse_time_window(choice[2:])
                if delta:
                    hours = int(delta.total_seconds() / 3600)
                    print(f"\nUpdating time window to {format_time_window(datetime.now() - delta, datetime.now())}...")
                    continue
                else:
                    print("Invalid time format. Examples: '24', '24h', '7d', '1w'")
                    continue
            elif choice.isdigit():
                index = int(choice)
                if 1 <= index <= len(matching_pods):
                    return matching_pods[index - 1]['name']
                print(f"Please enter a number between 1 and {len(matching_pods)}")
            else:
                pod_pattern = choice
                continue

    def run_diagnostics(self, cluster: str, namespace: str, pod_name: str, hours: int = 24, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None):
        """Run full diagnostics on a pod"""
        print(f"\nRunning diagnostics for pod: {pod_name}")
        print(f"Cluster: {cluster}")
        print(f"Namespace: {namespace}")
        if start_time and end_time:
            print(f"Time window: {start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')}")
            print(f"Duration: {format_time_window(start_time, end_time)}\n")
        else:
            print(f"Time window: Last {hours} hours\n")

        # Get metrics
        metrics = self.get_pod_metrics(cluster, namespace, pod_name, hours, start_time, end_time)

        # Determine status from both events and metrics
        events = self.get_pod_events(cluster, namespace, pod_name, hours, start_time, end_time)
        event_status = self._determine_pod_status_from_events(events)
        metric_status = self._determine_pod_status_from_metrics(metrics)

        # Prefer event status unless unknown
        pod_status = event_status if event_status != 'Unknown' else metric_status
        metrics['status'] = pod_status

        # Display results
        self.display_metrics(metrics)
        self.display_events(events)

        # Generate and display recommendations
        recommendations = self.analyze_metrics(metrics)
        if recommendations:
            print("\nRecommendations:")
            self.display_recommendations(recommendations)

    def display_metrics(self, metrics: Dict):
        """Display pod metrics in a formatted table"""
        if not metrics:
            print("\nNo metrics available")
            return

        # Headers
        print("Pod Metrics:")
        headers = ["Resource", "Current", "Peak", "Average", "Min", "Request", "Limit", "Utilization"]
        print("╔" + "═" * 12 + "╦" + "═" * 15 + "╦" + "═" * 15 + "╦" + "═" * 15 + "╦" + "═" * 15 + "╦" + "═" * 15 + "╦" + "═" * 15 + "╦" + "═" * 31 + "╗")
        print(f"║ {headers[0]:<12} ║ {headers[1]:<15} ║ {headers[2]:<15} ║ {headers[3]:<15} ║ {headers[4]:<15} ║ {headers[5]:<15} ║ {headers[6]:<15} ║ {headers[7]:<31} ║")
        print("╠" + "═" * 12 + "╬" + "═" * 15 + "╬" + "═" * 15 + "╬" + "═" * 15 + "╬" + "═" * 15 + "╬" + "═" * 15 + "╬" + "═" * 15 + "╬" + "═" * 31 + "╣")

        # Memory metrics
        if 'memory' in metrics and metrics['memory'] is not None:
            memory = metrics['memory']
            memory_limit = 0
            memory_request = 0
            
            if 'memory_limit' in metrics and metrics['memory_limit'] is not None:
                memory_limit = metrics['memory_limit'].get('current', 0)
            
            if 'memory_request' in metrics and metrics['memory_request'] is not None:
                memory_request = metrics['memory_request'].get('current', 0)

            # Format memory values
            current = format_memory_size(memory['current'])
            peak = format_memory_size(memory['max'])
            avg = format_memory_size(memory['avg'])
            min_val = format_memory_size(memory['min'])
            request = format_memory_size(memory_request) if memory_request > 0 else "Not set"
            limit = format_memory_size(memory_limit) if memory_limit > 0 else "Not set"

            utilization = "N/A"
            if memory_limit > 0:
                util_current = (memory['current'] / memory_limit) * 100
                util_peak = (memory['max'] / memory_limit) * 100
                utilization = f"{util_current:.1f}% (peak: {util_peak:.1f}%)"
            elif all(v == 0 for v in [memory['current'], memory['max'], memory['avg'], memory['min']]):
                utilization = "No activity"

            print(f"║ {'Memory':<12} ║ {current:<15} ║ {peak:<15} ║ {avg:<15} ║ {min_val:<15} ║ {request:<15} ║ {limit:<15} ║ {utilization:<31} ║")
        else:
            print(f"║ {'Memory':<12} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'No data available':<31} ║")

        # CPU metrics
        if 'cpu' in metrics and metrics['cpu'] is not None:
            cpu = metrics['cpu']
            cpu_limit = 0
            cpu_request = 0
            
            if 'cpu_limit' in metrics and metrics['cpu_limit'] is not None:
                cpu_limit = metrics['cpu_limit'].get('current', 0)
            
            if 'cpu_request' in metrics and metrics['cpu_request'] is not None:
                cpu_request = metrics['cpu_request'].get('current', 0)

            # Format CPU values (nanocores)
            current = self.format_cpu(cpu['current'])
            peak = self.format_cpu(cpu['max'])
            avg = self.format_cpu(cpu['avg'])
            min_val = self.format_cpu(cpu['min'])
            request = self.format_cpu(cpu_request) if cpu_request > 0 else "Not set"
            limit = self.format_cpu(cpu_limit) if cpu_limit > 0 else "Not set"

            utilization = "N/A"
            if cpu_limit > 0:
                util_current = (cpu['current'] / cpu_limit) * 100
                util_peak = (cpu['max'] / cpu_limit) * 100
                utilization = f"{util_current:.1f}% (peak: {util_peak:.1f}%)"
            elif all(v == 0 for v in [cpu['current'], cpu['max'], cpu['avg'], cpu['min']]):
                utilization = "No activity"

            print(f"║ {'CPU':<12} ║ {current:<15} ║ {peak:<15} ║ {avg:<15} ║ {min_val:<15} ║ {request:<15} ║ {limit:<15} ║ {utilization:<31} ║")
        else:
            print(f"║ {'CPU':<12} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'No data available':<31} ║")

        print("╚" + "═" * 12 + "╩" + "═" * 15 + "╩" + "═" * 15 + "╩" + "═" * 15 + "╩" + "═" * 15 + "╩" + "═" * 15 + "╩" + "═" * 15 + "╩" + "═" * 31 + "╝")

    def analyze_metrics(self, metrics: Dict) -> List[Dict]:
        """Analyze metrics and generate recommendations"""
        recommendations = []

        if not metrics:
            return recommendations

        # CPU recommendations
        if 'cpu' in metrics and metrics['cpu'] is not None:
            cpu = metrics['cpu']
            cpu_limit = 0
            
            if 'cpu_limit' in metrics and metrics['cpu_limit'] is not None:
                cpu_limit = metrics['cpu_limit'].get('current', 0)

            # Convert to millicores for calculation
            current_millicores = cpu['current'] / 1_000_000
            peak_millicores = cpu['max'] / 1_000_000
            avg_millicores = cpu['avg'] / 1_000_000

            # Calculate utilization percentages with safety checks
            if all(v == 0 for v in [cpu['current'], cpu['max'], cpu['avg'], cpu['min']]):
                recommendations.append({
                    'resource': 'CPU',
                    'type': 'Info',
                    'message': 'No CPU activity detected in the specified time window.'
                })
            elif peak_millicores > 90 and cpu_limit > 0:  # High utilization
                recommendations.append({
                    'resource': 'CPU',
                    'type': 'Warning',
                    'message': f'High CPU utilization (current: {current_millicores:.1f}m, peak: {peak_millicores:.1f}m, avg: {avg_millicores:.1f}m). Consider increasing limits.'
                })
            elif peak_millicores < 10 and cpu_limit > 0:  # Very low utilization
                recommendations.append({
                    'resource': 'CPU',
                    'type': 'Info',
                    'message': f'Low CPU utilization (peak: {peak_millicores:.1f}m). Consider reducing limits to optimize resource allocation.'
                })
            elif cpu_limit == 0:  # No CPU limit set
                recommendations.append({
                    'resource': 'CPU',
                    'type': 'Info',
                    'message': f'No CPU limit set. Current usage: {current_millicores:.1f}m, peak: {peak_millicores:.1f}m, avg: {avg_millicores:.1f}m.'
                })

        # Memory recommendations
        if 'memory' in metrics and metrics['memory'] is not None:
            memory = metrics['memory']
            memory_limit = 0
            
            if 'memory_limit' in metrics and metrics['memory_limit'] is not None:
                memory_limit = metrics['memory_limit'].get('current', 0)

            # Memory usage in MB for readability
            current_mb = memory['current'] / (1024 * 1024)
            peak_mb = memory['max'] / (1024 * 1024)
            avg_mb = memory['avg'] / (1024 * 1024)

            if all(v == 0 for v in [memory['current'], memory['max'], memory['avg'], memory['min']]):
                recommendations.append({
                    'resource': 'Memory',
                    'type': 'Info',
                    'message': 'No memory activity detected in the specified time window.'
                })
            elif memory_limit > 0:
                # Calculate utilization percentages
                peak_util = (memory['max'] / memory_limit) * 100
                avg_util = (memory['avg'] / memory_limit) * 100

                if peak_util > 90:  # High utilization
                    recommendations.append({
                        'resource': 'Memory',
                        'type': 'Warning',
                        'message': f'High memory utilization (current: {current_mb:.1f}MB, peak: {peak_mb:.1f}MB, {peak_util:.1f}%). Consider increasing limits.'
                    })
                elif peak_util < 10:  # Very low utilization
                    recommendations.append({
                        'resource': 'Memory',
                        'type': 'Info',
                        'message': f'Low memory utilization (peak: {peak_util:.1f}%). Consider reducing limits to optimize resource allocation.'
                    })
            else:  # No memory limit set
                recommendations.append({
                    'resource': 'Memory',
                    'type': 'Info',
                    'message': f'No memory limit set. Current usage: {current_mb:.1f}MB, peak: {peak_mb:.1f}MB, avg: {avg_mb:.1f}MB.'
                })

        return recommendations

    def display_recommendations(self, recommendations: List[Dict]):
        """Display recommendations in a well-formatted table"""
        if not recommendations:
            print("\nNo recommendations at this time")
            return

        print("\nRecommendations:")
        headers = ["Resource", "Type", "Recommendation"]
        rows = [[r['resource'], r['type'].title(), r['message']] for r in recommendations]
        print(tabulate(rows, headers=headers, tablefmt="double_grid"))

    def display_events(self, events: List[Dict]):
        """Display pod events in a well-formatted table"""
        if not events:
            print("\nNo events found for the specified pod")
            return

        print("\nRecent Events:")
        headers = ["Timestamp", "Type", "Title", "Status"]
        rows = []
        for event in events[:5]:  # Show 5 most recent events for better context
            try:
                # Safely get event attributes with fallbacks
                timestamp = event.get('date_happened', None)
                timestamp_str = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S') if timestamp else "Unknown"

                # Handle alert_type which is an enum or dict
                alert_type = event.get('alert_type', 'info')
                if isinstance(alert_type, dict):
                    alert_type = alert_type.get('value', 'info')
                alert_type = str(alert_type).capitalize()

                # Get title and text safely
                title = str(event.get('title', 'No title'))
                text = str(event.get('text', '')).lower()

                # Determine event status
                title_lower = title.lower()
                status = 'Info'
                if 'error' in text or 'failed' in text or 'error' in title_lower or 'failed' in title_lower:
                    status = 'Error'
                elif 'warning' in text or 'warning' in title_lower:
                    status = 'Warning'
                elif 'success' in text or 'started' in text or 'running' in text:
                    status = 'Success'

                rows.append([
                    timestamp_str,
                    alert_type,
                    title[:80] + '...' if len(title) > 80 else title,  # Truncate long titles
                    status
                ])
            except Exception as e:
                # Log any errors but continue processing other events
                print(f"Warning: Error processing event: {str(e)}")
                continue

        if not rows:
            print("No valid events found to display")
            return

        # Use double-line borders for better readability
        print("╔" + "═" * 20 + "╦" + "═" * 10 + "╦" + "═" * 82 + "╦" + "═" * 10 + "╗")
        print(f"║ {headers[0]:<20} ║ {headers[1]:<10} ║ {headers[2]:<82} ║ {headers[3]:<10} ║")
        print("╠" + "═" * 20 + "╬" + "═" * 10 + "╬" + "═" * 82 + "╬" + "═" * 10 + "╣")

        for row in rows:
            print(f"║ {row[0]:<20} ║ {row[1]:<10} ║ {row[2]:<82} ║ {row[3]:<10} ║")

        print("╚" + "═" * 20 + "╩" + "═" * 10 + "╩" + "═" * 82 + "╩" + "═" * 10 + "╝")

        # Show detailed messages for events with additional context
        print("\nDetailed Event Messages:")
        for event in events[:5]:
            try:
                timestamp = event.get('date_happened', None)
                timestamp_str = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S') if timestamp else "Unknown"

                alert_type = event.get('alert_type', 'info')
                if isinstance(alert_type, dict):
                    alert_type = alert_type.get('value', 'info')
                alert_type = str(alert_type).capitalize()

                title = str(event.get('title', 'No title'))
                text = str(event.get('text', ''))

                if text.lower() == 'none':
                    text = None

                print(f"\n[{timestamp_str} - {alert_type}]")
                print(f"Title: {title}")
                if text:
                    print(f"Details: {text}")
            except Exception as e:
                print(f"Warning: Error displaying event details: {str(e)}")
                continue

    def _determine_pod_status_from_events(self, events: List[Dict]) -> str:
        """Determine pod status from events"""
        if not events:
            return 'Unknown'

        try:
            # Sort events by date_happened in descending order (newest first)
            sorted_events = sorted(
                events,
                key=lambda x: x.get('date_happened', 0),
                reverse=True
            )

            # Look at the last 5 events to get better context
            for event in sorted_events[:5]:
                try:
                    # Safely get event text and title
                    text = str(event.get('text', '')).lower()
                    title = str(event.get('title', '')).lower()

                    # Handle alert_type which might be an enum or dict
                    alert_type = event.get('alert_type', '')
                    if isinstance(alert_type, dict):
                        alert_type = alert_type.get('value', '')
                    alert_type = str(alert_type).lower()

                    full_text = f"{title} {text} {alert_type}"

                    # Check for completion patterns first
                    if any(pattern in full_text for pattern in [
                        'exit code 0',
                        'completed successfully',
                        'task succeeded',
                        'success',
                        'completed',
                        'task done',
                        'finished'
                    ]):
                        return 'Completed'

                    # Check for failure patterns
                    if any(pattern in full_text for pattern in [
                        'error',
                        'failed',
                        'crash',
                        'exit code 1',
                        'termination',
                        'killed',
                        'oomkilled'
                    ]):
                        return 'Failed'

                    # Only consider a pod active if it has meaningful CPU activity
                    # This prevents pods that are just starting up or shutting down
                    # from being marked as active
                    if any(pattern in full_text for pattern in [
                        'started container',
                        'created container',
                        'assigned',
                        'scheduled'
                    ]):
                        # Return Unknown to let metrics determine the actual status
                        return 'Unknown'

                except Exception as e:
                    print(f"Warning: Error processing event for status detection: {str(e)}")
                    continue

            # If no definitive status found in events, check CPU metrics
            return 'Unknown'

        except Exception as e:
            print(f"Warning: Error during status detection: {str(e)}")
            return 'Unknown'

    def _determine_pod_status_from_metrics(self, metrics: Dict) -> str:
        """Determine pod status from metrics data"""
        if not metrics:
            return 'Unknown'

        # First check if we have explicit pod status
        if 'pod_status' in metrics:
            status = metrics['pod_status']
            if status.get('current', 0) == 1:
                return 'Active'
            elif status.get('current', 0) == 0:
                return 'Completed'

        # Fall back to CPU metrics if no pod status
        if 'cpu' in metrics and metrics['cpu'] is not None:
            cpu = metrics['cpu']
            current_cpu = cpu.get('current', 0)
            if current_cpu > 1_000_000:  # More than 1m CPU
                return 'Active'
            elif cpu.get('max', 0) > 0:
                return 'Completed'

        # Fall back to memory metrics
        if 'memory' in metrics and metrics['memory'] is not None:
            memory = metrics['memory']
            current_memory = memory.get('current', 0)
            if current_memory > 0:
                return 'Active'

        return 'Unknown'

    def _print_table(self, headers: List[str], rows: List[List[str]]):
        """Print a table with double-line borders for better readability"""
        # Calculate column widths
        widths = [max(len(str(row[i])) for row in [headers] + rows) for i in range(len(headers))]

        # Top border
        print("╔" + "╦".join("═" * (w + 2) for w in widths) + "╗")

        # Headers
        header_cells = [f" {str(headers[i]):<{widths[i]}} " for i in range(len(headers))]
        print("║" + "║".join(header_cells) + "║")

        # Header-data separator
        print("╠" + "╬".join("═" * (w + 2) for w in widths) + "╣")

        # Data rows
        for row in rows:
            cells = [f" {str(row[i]):<{widths[i]}} " for i in range(len(row))]
            print("║" + "║".join(cells) + "║")

        # Bottom border
        print("╚" + "╩".join("═" * (w + 2) for w in widths) + "╝")

    def _process_points(self, points: List[float]) -> Dict[str, float]:
        """Process a list of metric points and return statistics"""
        if not points:
            return {'current': 0, 'max': 0, 'min': 0, 'avg': 0}

        # Convert any Point objects to their values
        values = []
        for point in points:
            try:
                # Handle Datadog Point objects
                if hasattr(point, '_data_store') and 'value' in point._data_store:
                    # Point objects store [timestamp, value] in _data_store['value']
                    point_data = point._data_store['value']
                    if isinstance(point_data, (list, tuple)) and len(point_data) > 1:
                        value = point_data[1]  # Second element is the value
                        if isinstance(value, (int, float)):
                            values.append(float(value))
                        else:
                            print(f"Warning: Invalid value type in Point data: {type(value)}")
                # Handle list/tuple format
                elif isinstance(point, (list, tuple)):
                    if len(point) > 1:
                        value = point[1]  # Second element is the value
                        if isinstance(value, (int, float)):
                            values.append(float(value))
                        else:
                            print(f"Warning: Invalid value type in list: {type(value)}")
                # Handle direct value format
                elif isinstance(point, (int, float)):
                    values.append(float(point))
                else:
                    print(f"Warning: Skipping point with unexpected format: {type(point)}")
            except Exception as e:
                print(f"Warning: Error processing point: {str(e)}")
                if hasattr(point, '__dict__'):
                    print(f"Debug: Point attributes: {point.__dict__}")
                continue

        if not values:
            return {'current': 0, 'max': 0, 'min': 0, 'avg': 0}

        # Filter out any invalid values
        valid_values = [v for v in values if v is not None and not math.isnan(v)]
        if not valid_values:
            return {'current': 0, 'max': 0, 'min': 0, 'avg': 0}

        return {
            'current': valid_values[-1],
            'max': max(valid_values),
            'min': min(valid_values),
            'avg': sum(valid_values) / len(valid_values)
        }

    def _format_bytes(self, value: float) -> str:
        """Format bytes in human readable format"""
        if value == 0:
            return "0 B"

        units = ['B', 'KB', 'MB', 'GB', 'TB']
        size = float(value)
        unit_index = 0

        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1

        return f"{size:.1f} {units[unit_index]}"

    def _format_resource_recommendations(self, metrics: Dict) -> List[Dict]:
        """Generate resource recommendations based on metrics"""
        recommendations = []

        # CPU recommendations
        if 'cpu' in metrics:
            cpu = metrics['cpu']
            cpu_limit = metrics.get('cpu_limit', {}).get('current', 0)
            cpu_request = metrics.get('cpu_request', {}).get('current', 0)

            # Convert to millicores for easier reading
            current_mcores = cpu['current'] / 1_000_000
            peak_mcores = cpu['max'] / 1_000_000
            avg_mcores = cpu['avg'] / 1_000_000

            if all(v == 0 for v in [cpu['current'], cpu['max'], cpu['avg'], cpu['min']]):
                recommendations.append({
                    'resource': 'CPU',
                    'type': 'Info',
                    'message': 'No CPU activity detected in the specified time window.'
                })
            elif cpu_limit == 0:
                # No CPU limit set, provide guidance based on actual usage
                recommendations.append({
                    'resource': 'CPU',
                    'type': 'Warning',
                    'message': f'No CPU limits set. Current usage: {current_mcores:.1f}m (peak: {peak_mcores:.1f}m, avg: {avg_mcores:.1f}m). Consider setting limits based on peak usage.'
                })
            else:
                # Calculate utilization with safety checks
                limit_mcores = cpu_limit / 1_000_000
                if limit_mcores > 0:
                    util_current = min((current_mcores / limit_mcores) * 100, 100)
                    util_peak = min((peak_mcores / limit_mcores) * 100, 100)

                    if util_peak > 90:  # High utilization
                        recommendations.append({
                            'resource': 'CPU',
                            'type': 'Warning',
                            'message': f'High CPU utilization (current: {util_current:.1f}%, peak: {util_peak:.1f}%). Consider increasing limits.'
                        })
                    elif util_peak < 10:  # Very low utilization
                        recommendations.append({
                            'resource': 'CPU',
                            'type': 'Info',
                            'message': f'Low CPU utilization (peak: {util_peak:.1f}%). Consider reducing limits.'
                        })

            # Add request recommendation if needed
            if cpu_request == 0 and avg_mcores > 0:
                recommendations.append({
                    'resource': 'CPU',
                    'type': 'Info',
                    'message': f'No CPU request set. Consider setting request to {avg_mcores:.1f}m based on average usage.'
                })

        # Memory recommendations
        if 'memory' in metrics:
            memory = metrics['memory']
            memory_limit = metrics.get('memory_limit', {}).get('current', 0)
            memory_request = metrics.get('memory_request', {}).get('current', 0)

            if memory_limit > 0:
                current_util = (memory['current'] / memory_limit) * 100
                peak_util = (memory['max'] / memory_limit) * 100

                if peak_util < 10:  # Very low utilization
                    recommendations.append({
                        'resource': 'Memory',
                        'type': 'Info',
                        'message': f'Low memory utilization (current: {current_util:.1f}%, peak: {peak_util:.1f}%). Consider reducing limits.'
                    })
                elif peak_util > 90:  # High utilization
                    recommendations.append({
                        'resource': 'Memory',
                        'type': 'Warning',
                        'message': f'High memory utilization (peak: {peak_util:.1f}%). Consider increasing limits.'
                    })
            else:
                # No memory limit set, show actual usage
                if memory['avg'] > 0:
                    recommendations.append({
                        'resource': 'Memory',
                        'type': 'Warning',
                        'message': f'No memory limits set. Peak usage: {format_memory_size(memory["max"])}. Consider setting appropriate limits.'
                    })

            # Add request recommendation if needed
            if memory_request == 0 and memory['avg'] > 0:
                recommendations.append({
                    'resource': 'Memory',
                    'type': 'Info',
                    'message': f'No memory request set. Consider setting request to {format_memory_size(memory["avg"])} based on average usage.'
                })

        return recommendations

    def _format_metrics_table(self, metrics: Dict, resource_type: str, unit_formatter=None) -> str:
        """Format metrics into a table row"""
        if not metrics:
            return f"| {resource_type:<10} | {'N/A':<13} | {'N/A':<13} | {'N/A':<13} | {'N/A':<13} | {'N/A':<29} |"

        current = metrics['current']
        peak = metrics['max']
        avg = metrics['avg']
        min_val = metrics['min']

        if unit_formatter:
            current_str = unit_formatter(current)
            peak_str = unit_formatter(peak)
            avg_str = unit_formatter(avg)
            min_str = unit_formatter(min_val)
        else:
            current_str = f"{current:.1f}"
            peak_str = f"{peak:.1f}"
            avg_str = f"{avg:.1f}"
            min_str = f"{min_val:.1f}"

        # Calculate utilization if limit metrics are available
        utilization = "N/A"
        if resource_type.lower() == 'memory' and 'memory_limit' in self.metrics:
            limit = self.metrics['memory_limit']['current']
            if limit > 0:
                util_current = (current / limit) * 100
                util_peak = (peak / limit) * 100
                utilization = f"{util_current:.1f}% (peak: {util_peak:.1f}%)"
        elif resource_type.lower() == 'cpu' and 'cpu_limit' in self.metrics:
            limit = self.metrics['cpu_limit']['current']
            if limit > 0:
                # Convert nanocores to millicores for both current values and limits
                current_millicores = current / 1_000_000
                peak_millicores = peak / 1_000_000
                limit_millicores = limit / 1_000_000
                util_current = (current_millicores / limit_millicores) * 100
                util_peak = (peak_millicores / limit_millicores) * 100
                utilization = f"{util_current:.1f}% (peak: {util_peak:.1f}%)"

        return f"| {resource_type:<10} | {current_str:<13} | {peak_str:<13} | {avg_str:<13} | {min_str:<13} | {utilization:<29} |"

def parse_time_window(time_str: str) -> Optional[timedelta]:
    """Parse time window string into timedelta
    Supports formats:
    - Nx where N is a number and x is one of: h (hours), d (days), w (weeks)
    - N where N is a number (assumed to be hours)
    """
    if not time_str:
        return None

    # Remove any whitespace
    time_str = time_str.strip().lower()

    # Try to parse as simple hours
    try:
        return timedelta(hours=float(time_str))
    except ValueError:
        pass

    # Try to parse as duration with unit
    units = {
        'h': 'hours',
        'd': 'days',
        'w': 'weeks'
    }

    for unit, unit_name in units.items():
        if time_str.endswith(unit):
            try:
                value = float(time_str[:-1])
                return timedelta(**{unit_name: value})
            except ValueError:
                continue

    return None

def format_time_window(start_time: datetime, end_time: datetime) -> str:
    """Format time window in a human-readable way"""
    duration = end_time - start_time
    hours = duration.total_seconds() / 3600

    if hours < 24:
        return f"{int(hours)} hours"
    elif hours < 168:  # 7 days
        days = int(hours / 24)
        remaining_hours = int(hours % 24)
        if remaining_hours:
            return f"{days} days {remaining_hours} hours"
        return f"{days} days"
    else:
        weeks = int(hours / 168)
        remaining_days = int((hours % 168) / 24)
        if remaining_days:
            return f"{weeks} weeks {remaining_days} days"
        return f"{weeks} weeks"

def main():
    parser = argparse.ArgumentParser(description='Get pod metrics and diagnostics')
    parser.add_argument('cluster', help='Kubernetes cluster name')
    parser.add_argument('namespace', help='Kubernetes namespace')
    parser.add_argument('pod_pattern', help='Pod name or pattern to match')

    # Time window options with examples
    time_group = parser.add_argument_group('Time window options')
    time_group.add_argument('--hours', '-t', type=str,
                           help='Time window (e.g., "24" or "24h" for hours, "7d" for days, "1w" for weeks)')
    time_group.add_argument('--start-time', help='Start time in YYYY-MM-DD HH:MM format')
    time_group.add_argument('--end-time', help='End time in YYYY-MM-DD HH:MM format (defaults to current time)')

    args = parser.parse_args()

    # Handle time window
    now = datetime.now()
    start_time = None
    end_time = None
    hours = 24  # Default for relative time references

    if args.start_time or args.end_time:
        try:
            # If only end time is given, default start time to 24 hours before
            if args.end_time and not args.start_time:
                end_time = datetime.strptime(args.end_time, '%Y-%m-%d %H:%M')
                start_time = end_time - timedelta(hours=24)
            # If only start time is given, end time is now
            elif args.start_time and not args.end_time:
                start_time = datetime.strptime(args.start_time, '%Y-%m-%d %H:%M')
                end_time = now
            # Both times given
            else:
                start_time = datetime.strptime(args.start_time, '%Y-%m-%d %H:%M')
                end_time = datetime.strptime(args.end_time, '%Y-%m-%d %H:%M')
                if end_time < start_time:
                    print("Error: End time must be after start time")
                    return
            # Calculate hours for compatibility with relative time functions
            hours = max(1, int((end_time - start_time).total_seconds() / 3600))
        except ValueError:
            print("Error: Time must be in YYYY-MM-DD HH:MM format")
            return
    else:
        # Parse time window with units
        if args.hours:
            delta = parse_time_window(args.hours)
            if not delta:
                print("Error: Invalid time format. Examples: '24', '24h', '7d', '1w'")
                return
            hours = int(delta.total_seconds() / 3600)
            start_time = now - timedelta(hours=hours)
            end_time = now
        else:
            start_time = now - timedelta(hours=hours)
            end_time = now

    diagnostics = PodDiagnostics()

    # First find and select the pod
    selected_pod = diagnostics.select_pod(args.cluster, args.namespace, args.pod_pattern, hours, start_time, end_time)
    if not selected_pod:
        print("No pod selected. Exiting.")
        return

    # Run diagnostics with the time window
    diagnostics.run_diagnostics(args.cluster, args.namespace, selected_pod, hours, start_time, end_time)

if __name__ == '__main__':
    main()
