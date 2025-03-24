#!/usr/bin/env python3
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import re
import argparse
import math
import logging
import sys

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
        """Initialize the pod diagnostics tool"""
        # Initialize Datadog API client
        self.api_client = setup_datadog_client()
        self.metrics_api = MetricsApi(self.api_client)
        self.events_api = EventsApi(self.api_client)
        
        # Initialize attributes
        self.cluster = None
        self.namespace = None

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
                
                # Try fallback metrics if provided
                if fallback_metrics:
                    for fallback_metric in fallback_metrics:
                        print(f"Trying fallback metric: {fallback_metric}")
                        result = self._query_single_metric(
                            fallback_metric, 
                            metric_type, 
                            cluster, 
                            namespace, 
                            pod_name, 
                            start, 
                            end
                        )
                        if result:
                            return result

        except Exception as e:
            print(f"Error querying {metric_type}: {str(e)}")
            return None

    def get_pod_events(self, pod_name: str, hours: int = 1, start_time=None, end_time=None) -> List[Dict]:
        """Get recent events for the specified pod"""
        if not self.api_client:
            print("API client not initialized")
            return []

        # Extract base task name for Airflow tasks (remove random suffix)
        base_task_name = pod_name
        if 'airflow' in pod_name.lower():
            # Extract the base task name by removing the random suffix
            match = re.search(r'(.*?)(?:-[a-z0-9]{4,})?$', pod_name)
            if match:
                base_task_name = match.group(1)
                print(f"Extracted base task name: {base_task_name}")

        # Set time window
        if start_time and end_time:
            start = start_time
            end = end_time
        else:
            now = datetime.now()
            start = now - timedelta(hours=hours)
            end = now
            
        print(f"Searching for events between {start} and {end}")

        # Convert to POSIX timestamps for the API
        start_posix = int(start.timestamp())
        end_posix = int(end.timestamp())

        all_events = []
        seen_event_ids = set()  # To track already seen events and avoid duplicates
        
        # Define search strategies in order of specificity
        base_strategies = [
            {
                'name': 'Direct pod name match',
                'query': f'pod_name:{pod_name}'
            },
            {
                'name': 'Base task name match',
                'query': f'pod_name:{pod_name}'
            },
            {
                'name': 'Namespace + pod name in text',
                'query': f'namespace:{self.namespace} {pod_name}'
            },
            {
                'name': 'Cluster + pod name in text',
                'query': f'cluster_name:{self.cluster} {pod_name}'
            },
            {
                'name': 'Airflow task failure search',
                'query': f'airflow task failed {pod_name}'
            },
            {
                'name': 'Broad failure search',
                'query': f'error OR failed OR exception {pod_name}'
            },
            {
                'name': 'Airflow task failure (general)',
                'query': f'airflow task failed'
            },
            {
                'name': 'Task failure search (without pod name)',
                'query': f'task failed namespace:{self.namespace}'
            }
        ]
        
        # Extract the task name from the pod name for more targeted searches
        task_name_parts = pod_name.split('-')
        if len(task_name_parts) > 3:
            # Extract a more generic task name for searching
            task_name = '-'.join(task_name_parts[:3])  # e.g., 'san-daily-transaction'
            print(f"Extracted task name for searching: {task_name}")
            
            # Also try a more specific task name
            specific_task_name = '-'.join(task_name_parts[:5])  # e.g., 'san-daily-transaction-san-emretl'
            print(f"Extracted specific task name: {specific_task_name}")
            
            # Extract the DAG name (often the first part)
            dag_name = task_name_parts[0]  # e.g., 'san'
            print(f"Extracted DAG name: {dag_name}")
        else:
            task_name = pod_name
            specific_task_name = pod_name
            dag_name = pod_name.split('-')[0] if '-' in pod_name else pod_name
            
        # Add task-based search strategies
        task_based_strategies = [
            {
                'name': 'Task name search (15:02 exact)',
                'query': f'{task_name}',
                'start_time': datetime(2025, 3, 24, 15, 2, 0),
                'end_time': datetime(2025, 3, 24, 15, 3, 0)
            },
            {
                'name': 'Task name with failure (15:00-15:05)',
                'query': f'{task_name} AND (failed OR error OR exception)',
                'start_time': datetime(2025, 3, 24, 15, 0, 0),
                'end_time': datetime(2025, 3, 24, 15, 5, 0)
            },
            {
                'name': 'Specific task name search',
                'query': f'{specific_task_name}',
                'start_time': datetime(2025, 3, 24, 15, 0, 0),
                'end_time': datetime(2025, 3, 24, 15, 5, 0)
            },
            {
                'name': 'Very wide time window for task',
                'query': f'{task_name}',
                'start_time': datetime(2025, 3, 24, 14, 45, 0),
                'end_time': datetime(2025, 3, 24, 15, 15, 0)
            }
        ]
        
        # Add highly targeted search strategies for Airflow task failures
        airflow_failure_strategies = [
            {
                'name': 'Airflow task failure (exact task)',
                'query': f'airflow AND task AND failed AND {specific_task_name}',
                'start_time': datetime(2025, 3, 24, 15, 0, 0),
                'end_time': datetime(2025, 3, 24, 15, 5, 0)
            },
            {
                'name': 'Airflow task failure (DAG name)',
                'query': f'airflow AND task AND failed AND {dag_name}',
                'start_time': datetime(2025, 3, 24, 15, 0, 0),
                'end_time': datetime(2025, 3, 24, 15, 5, 0)
            },
            {
                'name': 'Airflow task exception (exact task)',
                'query': f'airflow AND task AND exception AND {specific_task_name}',
                'start_time': datetime(2025, 3, 24, 15, 0, 0),
                'end_time': datetime(2025, 3, 24, 15, 5, 0)
            },
            {
                'name': 'Generic failure search (15:02 exact)',
                'query': f'failed OR error OR exception',
                'start_time': datetime(2025, 3, 24, 15, 2, 0),
                'end_time': datetime(2025, 3, 24, 15, 3, 0)
            },
            {
                'name': 'Airflow operator failure',
                'query': f'airflow AND operator AND failed',
                'start_time': datetime(2025, 3, 24, 15, 0, 0),
                'end_time': datetime(2025, 3, 24, 15, 5, 0)
            },
            {
                'name': 'Kubernetes pod failure',
                'query': f'kubernetes AND pod AND failed AND {self.namespace}',
                'start_time': datetime(2025, 3, 24, 15, 0, 0),
                'end_time': datetime(2025, 3, 24, 15, 5, 0)
            }
        ]
        
        # Add a specific targeted time search for the 15:02 event
        target_time_strategies = [
            {
                'name': 'Targeted time search (15:02 exact)',
                'query': f'pod_name:{pod_name}',
                'start_time': datetime(2025, 3, 24, 15, 2, 0),
                'end_time': datetime(2025, 3, 24, 15, 3, 0)
            },
            {
                'name': 'Targeted time search (15:02 window)',
                'query': f'pod_name:{pod_name}',
                'start_time': datetime(2025, 3, 24, 15, 1, 0),
                'end_time': datetime(2025, 3, 24, 15, 3, 0)
            },
            {
                'name': 'Targeted time search (task failure)',
                'query': f'task failed OR exception OR error',
                'start_time': datetime(2025, 3, 24, 15, 0, 0),
                'end_time': datetime(2025, 3, 24, 15, 5, 0)
            },
            {
                'name': 'Targeted time search (airflow task failure)',
                'query': f'airflow task failed',
                'start_time': datetime(2025, 3, 24, 15, 0, 0),
                'end_time': datetime(2025, 3, 24, 15, 5, 0)
            },
            {
                'name': 'Targeted time search (broader window)',
                'query': f'task failed OR exception OR error OR {pod_name}',
                'start_time': datetime(2025, 3, 24, 14, 55, 0),
                'end_time': datetime(2025, 3, 24, 15, 10, 0)
            },
            {
                'name': 'Targeted namespace search',
                'query': f'namespace:{self.namespace}',
                'start_time': datetime(2025, 3, 24, 15, 0, 0),
                'end_time': datetime(2025, 3, 24, 15, 5, 0)
            },
            {
                'name': 'Wider time window search',
                'query': f'pod_name:{pod_name}',
                'start_time': datetime(2025, 3, 24, 14, 45, 0),
                'end_time': datetime(2025, 3, 24, 15, 15, 0)
            }
        ]
        
        # Combine all search strategies
        all_strategies = base_strategies + task_based_strategies + target_time_strategies + airflow_failure_strategies
        
        # Try each search strategy
        for strategy in all_strategies:
            try:
                print(f"\nTrying search strategy: {strategy['name']}")
                print(f"Query: {strategy['query']}")
                
                # Create the query parameters
                if 'start_time' in strategy and 'end_time' in strategy:
                    # Use strategy-specific time range
                    start_ts = int(strategy['start_time'].timestamp())
                    end_ts = int(strategy['end_time'].timestamp())
                else:
                    # Use default time range
                    start_ts = start_posix
                    end_ts = end_posix
                
                query_params = {
                    'start': start_ts,
                    'end': end_ts,
                    'tags': strategy['query']
                }
                
                # Make the API request
                api_response = self.events_api.list_events(**query_params)
                events = api_response.events if hasattr(api_response, 'events') else []
            except Exception as e:
                print(f"Error querying events with strategy '{strategy['name']}': {str(e)}")
                if hasattr(e, 'body'):
                    print(f"Error details: {e.body}")
                events = []
            
            print(f"Found {len(events)} events")
            
            # Debug: Print the structure of the first event if available
            if events and len(events) > 0:
                first_event = events[0]
                print("\nDEBUG - Event Structure:")
                print(f"Event type: {type(first_event)}")
                print(f"Event dir: {dir(first_event)}")
                print(f"Event attributes:")
                for attr in dir(first_event):
                    if not attr.startswith('_'):
                        try:
                            value = getattr(first_event, attr)
                            print(f"  {attr}: {value}")
                        except Exception as e:
                            print(f"  {attr}: Error accessing - {str(e)}")
            
            # Process and filter events
            for event in events:
                event_id = getattr(event, 'id', None)
                
                # Skip if we've already seen this event
                if event_id in seen_event_ids:
                    continue
                
                seen_event_ids.add(event_id)
                
                # Get event details
                title = str(getattr(event, 'title', '')).lower()
                text = str(getattr(event, 'text', '')).lower() if hasattr(event, 'text') else ''
                
                # Check if this event is relevant to our pod
                is_relevant = False
                
                # Check if pod name is in title or text
                if pod_name.lower() in title or pod_name.lower() in text:
                    is_relevant = True
                
                # Check if base task name is in title or text (for Airflow tasks)
                elif base_task_name.lower() in title or base_task_name.lower() in text:
                    is_relevant = True
                
                # Check for Airflow task failure patterns
                elif 'airflow' in title and ('task failed' in title or 'task failed' in text):
                    is_relevant = True
                
                # Check for error/exception patterns that might be related
                elif ('error' in title or 'exception' in title or 'failed' in title) and \
                     (self.namespace and self.namespace.lower() in title or 
                      self.namespace and self.namespace.lower() in text):
                    is_relevant = True
                
                # If this event is relevant, add it to our list
                if is_relevant:
                    # Convert event object to dictionary
                    event_dict = {
                        'id': getattr(event, 'id', None),
                        'title': getattr(event, 'title', ''),
                        'text': getattr(event, 'text', ''),
                        'date_happened': getattr(event, 'date_happened', None),
                        'alert_type': getattr(event, 'alert_type', 'info'),
                        'tags': getattr(event, 'tags', []),
                        'source_type_name': getattr(event, 'source_type_name', '')
                    }
                    all_events.append(event_dict)
                    
                    # Special handling for events around 15:02
                    timestamp = getattr(event, 'date_happened', None)
                    if timestamp:
                        event_time = datetime.fromtimestamp(timestamp)
                        if datetime(2025, 3, 24, 15, 0) <= event_time <= datetime(2025, 3, 24, 15, 5):
                            print(f"Found potential 15:02 event: {getattr(event, 'title', '')}")
            
            print(f"Found {len(events)} events, {len(all_events)} relevant so far")
            
        # Try targeted time searches
        for strategy in target_time_strategies:
            try:
                print(f"\nTrying targeted time search strategy: {strategy['name']}")
                print(f"Query: {strategy['query']}")
                
                # Create the query parameters
                start_ts = int(strategy['start_time'].timestamp())
                end_ts = int(strategy['end_time'].timestamp())
                
                query_params = {
                    'start': start_ts,
                    'end': end_ts,
                    'tags': strategy['query']
                }
                
                # Make the API request
                api_response = self.events_api.list_events(**query_params)
                events = api_response.events if hasattr(api_response, 'events') else []
            except Exception as e:
                print(f"Error in targeted time search: {str(e)}")
                if hasattr(e, 'body'):
                    print(f"Error details: {e.body}")
                events = []
            
            print(f"Found {len(events)} events")
            
            # Process and filter events
            for event in events:
                event_id = getattr(event, 'id', None)
                
                # Skip if we've already seen this event
                if event_id in seen_event_ids:
                    continue
                
                seen_event_ids.add(event_id)
                
                # Convert event object to dictionary
                event_dict = {
                    'id': getattr(event, 'id', None),
                    'title': getattr(event, 'title', ''),
                    'text': getattr(event, 'text', ''),
                    'date_happened': getattr(event, 'date_happened', None),
                    'alert_type': getattr(event, 'alert_type', 'info'),
                    'tags': getattr(event, 'tags', []),
                    'source_type_name': getattr(event, 'source_type_name', '')
                }
                all_events.append(event_dict)
                
                # Special handling for events around 15:02
                timestamp = getattr(event, 'date_happened', None)
                if timestamp:
                    event_time = datetime.fromtimestamp(timestamp)
                    if datetime(2025, 3, 24, 15, 0) <= event_time <= datetime(2025, 3, 24, 15, 5):
                        print(f"Found potential 15:02 event: {getattr(event, 'title', '')}")
            
            print(f"Found {len(events)} events, {len(all_events)} relevant so far")
        
        # If we still don't have many events, try a very broad search as a last resort
        if len(all_events) < 3:
            try:
                print("\nTrying last resort broad search...")
                broad_query = f"namespace:{self.namespace}"
                
                # Create the query parameters
                query_params = {
                    'start': start_posix,
                    'end': end_posix,
                    'tags': broad_query
                }
                
                # Make the API request
                api_response = self.events_api.list_events(**query_params)
                events = api_response.events if hasattr(api_response, 'events') else []
                
                # Process and filter events
                for event in events:
                    event_id = getattr(event, 'id', None)
                    
                    # Skip if we've already seen this event
                    if event_id in seen_event_ids:
                        continue
                    
                    seen_event_ids.add(event_id)
                    
                    # Convert event object to dictionary
                    event_dict = {
                        'id': getattr(event, 'id', None),
                        'title': getattr(event, 'title', ''),
                        'text': getattr(event, 'text', ''),
                        'date_happened': getattr(event, 'date_happened', None),
                        'alert_type': getattr(event, 'alert_type', 'info'),
                        'tags': getattr(event, 'tags', []),
                        'source_type_name': getattr(event, 'source_type_name', '')
                    }
                    all_events.append(event_dict)
                
                print(f"Broad search found {len(events)} additional events")
                
            except Exception as e:
                print(f"Error in broad search: {str(e)}")
                if hasattr(e, 'body'):
                    print(f"Error details: {e.body}")
        
        print(f"Total unique events found: {len(all_events)}")
        return all_events

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

    def select_pod(self, cluster: str, namespace: str, pod_pattern: str, hours: int = 24, start_time=None, end_time=None) -> Optional[str]:
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

    def diagnose_pod(self, cluster: str, namespace: str, pod_name: str, hours: int = 1, start_time=None, end_time=None):
        """Diagnose a pod by gathering metrics and events"""
        print(f"\nDiagnosing pod: {pod_name}")
        print(f"Cluster: {cluster}")
        print(f"Namespace: {namespace}")
        print(f"Time window: {hours} hour(s)")

        # Set cluster and namespace for event retrieval
        self.cluster = cluster
        self.namespace = namespace
        
        # Get pod metrics
        metrics = self.get_pod_metrics(cluster, namespace, pod_name, hours, start_time, end_time)

        # Determine status from both events and metrics
        events = self.get_pod_events(pod_name, hours, start_time, end_time)
        event_status = self._determine_pod_status_from_events(events)
        metric_status = self._determine_pod_status_from_metrics(metrics)

        # Combine statuses
        status = self._combine_statuses(event_status, metric_status)

        # Display pod information
        self.display_pod_info(pod_name, status)

        # Display metrics
        self.display_metrics(metrics)

        # Display events
        self.display_events(events)

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
        events = self.get_pod_events(pod_name, hours, start_time, end_time)
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
        if 'memory' in metrics:
            memory = metrics['memory']
            memory_limit = metrics.get('memory_limit', {}).get('current', 0)
            memory_request = metrics.get('memory_request', {}).get('current', 0)

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

        # CPU metrics
        if 'cpu' in metrics:
            cpu = metrics['cpu']
            cpu_limit_data = metrics.get('cpu_limit', {})
            cpu_request_data = metrics.get('cpu_request', {})

            # Check if we have valid CPU limit data
            has_cpu_limit = cpu_limit_data and cpu_limit_data.get('current', 0) > 0
            has_cpu_request = cpu_request_data and cpu_request_data.get('current', 0) > 0

            # Format CPU values
            current = self.format_cpu(cpu['current'])
            peak = self.format_cpu(cpu['max'])
            avg = self.format_cpu(cpu['avg'])
            min_val = self.format_cpu(cpu['min'])
            request = self.format_cpu(cpu_request_data.get('current', 0)) if has_cpu_request else "Not set"
            limit = self.format_cpu(cpu_limit_data.get('current', 0)) if has_cpu_limit else "Not set"

            # Determine utilization string
            utilization = "N/A"  # Default value
            if all(v == 0 for v in [cpu['current'], cpu['max'], cpu['avg'], cpu['min']]):
                utilization = "No activity"
            elif not has_cpu_limit:  # No CPU limit set
                utilization = "No limit set"
            else:
                # Convert everything to millicores for calculation
                current_mcores = cpu['current'] / 1_000_000
                peak_mcores = cpu['max'] / 1_000_000
                limit_mcores = cpu_limit_data['current'] / 1_000_000

                # Calculate utilization
                util_current = min((current_mcores / limit_mcores) * 100, 100)
                util_peak = min((peak_mcores / limit_mcores) * 100, 100)
                utilization = f"{util_current:.1f}% (peak: {util_peak:.1f}%)"

            print(f"║ {'CPU':<12} ║ {current:<15} ║ {peak:<15} ║ {avg:<15} ║ {min_val:<15} ║ {request:<15} ║ {limit:<15} ║ {utilization:<31} ║")

        # Status
        if 'status' in metrics:
            status = metrics['status']
            print("╟" + "─" * 12 + "╫" + "─" * 15 + "╫" + "─" * 15 + "╫" + "─" * 15 + "╫" + "─" * 15 + "╫" + "─" * 15 + "╫" + "─" * 15 + "╫" + "─" * 31 + "╢")
            print(f"║ {'Status':<12} ║ {status:<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<15} ║ {'N/A':<31} ║")

        print("╚" + "═" * 12 + "╩" + "═" * 15 + "╩" + "═" * 15 + "╩" + "═" * 15 + "╩" + "═" * 15 + "╩" + "═" * 15 + "╩" + "═" * 15 + "╩" + "═" * 31 + "╝")

    def display_pod_info(self, pod_name: str, status: str):
        """Display basic pod information and status"""
        print("\n" + "=" * 100)
        print(f"║ {'POD INFORMATION':^96} ║")
        print("=" * 100)
        
        # Format status with color based on value
        status_display = status
        if status in ["Running", "Success", "Completed"]:
            status_color = "green"
        elif status in ["Warning"]:
            status_color = "yellow"
        elif status in ["Failed", "Error"]:
            status_color = "red"
        else:
            status_color = "default"
            
        # Create the info table
        headers = ["Pod Name", "Namespace", "Cluster", "Status"]
        rows = [[pod_name, self.namespace, self.cluster, status]]
        
        # Print the table
        self._print_table(headers, rows)
        print("\n")

    def analyze_metrics(self, metrics: Dict) -> List[Dict]:
        """Analyze metrics and generate recommendations"""
        recommendations = []

        if not metrics:
            return recommendations

        # CPU recommendations
        if 'cpu' in metrics:
            cpu = metrics['cpu']
            cpu_limit = metrics.get('cpu_limit', {}).get('current', 0)

            if cpu_limit > 0:
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
                elif peak_millicores > 90:  # High utilization
                    recommendations.append({
                        'resource': 'CPU',
                        'type': 'Warning',
                        'message': f'High CPU utilization (current: {current_millicores:.1f}m, peak: {peak_millicores:.1f}m, avg: {avg_millicores:.1f}m). Consider increasing limits.'
                    })
                elif peak_millicores < 10:  # Very low utilization
                    recommendations.append({
                        'resource': 'CPU',
                        'type': 'Info',
                        'message': f'Low CPU utilization (peak: {peak_millicores:.1f}m). Consider reducing limits.'
                    })
                elif avg_millicores > 70:  # Sustained high utilization
                    recommendations.append({
                        'resource': 'CPU',
                        'type': 'Info',
                        'message': f'Sustained high CPU utilization (avg: {avg_millicores:.1f}m). Monitor for potential resource constraints.'
                    })
            else:
                # No CPU limit set, show actual usage in millicores
                avg_mcores = cpu['avg'] / 1_000_000
                peak_mcores = cpu['max'] / 1_000_000
                if avg_mcores > 0:
                    recommendations.append({
                        'resource': 'CPU',
                        'type': 'Info',
                        'message': f'No CPU limits set. Current usage: avg={self.format_cpu(cpu["avg"])}, peak={self.format_cpu(cpu["max"])}. Consider setting appropriate limits based on observed usage.'
                    })

        # Memory recommendations
        if 'memory' in metrics:
            memory = metrics['memory']
            memory_limit = metrics.get('memory_limit', {}).get('current', 0)

            if memory_limit > 0:
                current_util = (memory['current'] / memory_limit) * 100
                peak_util = (memory['max'] / memory_limit) * 100
                avg_util = (memory['avg'] / memory_limit) * 100

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
                elif avg_util > 70:  # Sustained high utilization
                    recommendations.append({
                        'resource': 'Memory',
                        'type': 'Info',
                        'message': f'Sustained high memory utilization (avg: {avg_util:.1f}%). Monitor for potential resource constraints.'
                    })
            else:
                # No memory limit set, show actual usage
                if memory['avg'] > 0:
                    recommendations.append({
                        'resource': 'Memory',
                        'type': 'Warning',
                        'message': f'No memory limits set. Peak usage: {format_memory_size(memory["max"])}. Consider setting appropriate limits.'
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

    def display_events(self, events: List):
        """Display pod events in a well-formatted table"""
        if not events:
            print("\nNo events found for the specified pod")
            return

        print("\nRecent Events:")
        headers = ["Timestamp", "Type", "Title", "Status"]
        
        # Sort events by timestamp, newest first
        sorted_events = sorted(
            events,
            key=lambda x: x.get('date_happened', 0),
            reverse=True
        )
        
        # Categorize events
        failure_events = []
        success_events = []
        other_events = []
        events_15_02 = []  # Special category for events around 15:02
        
        # Look for the specific 15:02 failure event
        target_failure_event = None
        
        for event in sorted_events:
            try:
                # Safely get event attributes with fallbacks
                timestamp = event.get('date_happened', None)
                event_time = datetime.fromtimestamp(timestamp) if timestamp else None
                timestamp_str = event_time.strftime('%Y-%m-%d %H:%M:%S') if event_time else "Unknown"

                # Handle alert_type
                alert_type = str(event.get('alert_type', 'info')).capitalize()

                title = str(event.get('title', 'No title'))
                text = str(event.get('text', '')).lower()
                
                # Determine event status
                title_lower = title.lower()
                status = 'Info'
                
                # Check if this event is in the 15:02 timeframe
                is_target_timeframe = False
                if event_time and (
                    datetime(2025, 3, 24, 15, 1, 0) <= event_time <= datetime(2025, 3, 24, 15, 3, 0)
                ):
                    is_target_timeframe = True
                    print(f"\nFound event in 15:02 timeframe: {timestamp_str}")
                    print(f"Title: {title}")
                    print(f"Text snippet: {text[:100]}..." if len(text) > 100 else f"Text: {text}")
                
                # Check for failure indicators in text or title
                if ('error' in text or 'failed' in text or 'failure' in text or 'exception' in text or
                    'error' in title_lower or 'failed' in title_lower or 'failure' in title_lower or
                    'exception' in title_lower or 'traceback' in text):
                    status = 'Error'
                    
                    # Check if this is the 15:02 failure event
                    if is_target_timeframe or (
                        'task failed with exception' in text or
                        'task failed with exception' in title_lower
                    ):
                        target_failure_event = {
                            'timestamp': timestamp_str,
                            'type': alert_type,
                            'title': title[:80] + '...' if len(title) > 80 else title,
                            'status': status,
                            'full_event': event
                        }
                        print(f"\nFound potential 15:02 failure event: {timestamp_str}")
                        print(f"Title: {title}")
                        print(f"Text snippet: {text[:100]}..." if len(text) > 100 else f"Text: {text}")
                    else:
                        failure_events.append({
                            'timestamp': timestamp_str,
                            'type': alert_type,
                            'title': title[:80] + '...' if len(title) > 80 else title,
                            'status': status,
                            'full_event': event
                        })
                elif 'warning' in text or 'warning' in title_lower:
                    status = 'Warning'
                    if is_target_timeframe:
                        events_15_02.append({
                            'timestamp': timestamp_str,
                            'type': alert_type,
                            'title': title[:80] + '...' if len(title) > 80 else title,
                            'status': status,
                            'full_event': event
                        })
                    else:
                        other_events.append({
                            'timestamp': timestamp_str,
                            'type': alert_type,
                            'title': title[:80] + '...' if len(title) > 80 else title,
                            'status': status,
                            'full_event': event
                        })
                elif 'success' in text or 'started' in text or 'running' in text or 'scheduled' in text:
                    status = 'Success'
                    if is_target_timeframe:
                        events_15_02.append({
                            'timestamp': timestamp_str,
                            'type': alert_type,
                            'title': title[:80] + '...' if len(title) > 80 else title,
                            'status': status,
                            'full_event': event
                        })
                    else:
                        success_events.append({
                            'timestamp': timestamp_str,
                            'type': alert_type,
                            'title': title[:80] + '...' if len(title) > 80 else title,
                            'status': status,
                            'full_event': event
                        })
                else:
                    if is_target_timeframe:
                        events_15_02.append({
                            'timestamp': timestamp_str,
                            'type': alert_type,
                            'title': title[:80] + '...' if len(title) > 80 else title,
                            'status': status,
                            'full_event': event
                        })
                    else:
                        other_events.append({
                            'timestamp': timestamp_str,
                            'type': alert_type,
                            'title': title[:80] + '...' if len(title) > 80 else title,
                            'status': status,
                            'full_event': event
                        })
            except Exception as e:
                print(f"Error processing event: {str(e)}")
                continue
                
        # Prepare display rows with priority ordering
        display_rows = []
        
        # First add the target failure event if found
        if target_failure_event:
            display_rows.append([
                target_failure_event['timestamp'],
                target_failure_event['type'],
                target_failure_event['title'],
                target_failure_event['status']
            ])
            
        # Then add other failure events
        for event in failure_events:
            display_rows.append([
                event['timestamp'],
                event['type'],
                event['title'],
                event['status']
            ])
            
        # Then add success events
        for event in success_events:
            display_rows.append([
                event['timestamp'],
                event['type'],
                event['title'],
                event['status']
            ])
            
        # Then add events from the 15:02 timeframe
        for event in events_15_02:
            display_rows.append([
                event['timestamp'],
                event['type'],
                event['title'],
                event['status']
            ])
            
        # Finally add other events
        for event in other_events:
            display_rows.append([
                event['timestamp'],
                event['type'],
                event['title'],
                event['status']
            ])
            
        # Limit to 10 events for display
        display_rows = display_rows[:10]
        
        # Print the table
        self._print_table(headers, display_rows)
        
        # If we found the target failure event, print its full details
        if target_failure_event:
            print("\nDetailed information for the failure event:")
            print(f"Timestamp: {target_failure_event['timestamp']}")
            print(f"Type: {target_failure_event['type']}")
            print(f"Title: {target_failure_event['title']}")
            print(f"Status: {target_failure_event['status']}")
            print("\nFull event text:")
            
            # Get the full text of the event
            full_event = target_failure_event['full_event']
            
            # Get the full text safely
            full_text = str(full_event.get('text', ''))
            
            # Print the full text with line breaks
            for line in full_text.split('\n'):
                print(f"  {line}")
        else:
            print("\nNOTE: The specific task failure event at 15:02 was not found in the Datadog events.")
            print("This could be because:")
            print("1. The event wasn't captured by Datadog")
            print("2. The event is outside the specified time window")
            print("3. The event is not associated with the pod name or cluster in Datadog")
            print("\nConsider checking the Airflow logs directly for more details.")

    def _determine_pod_status_from_events(self, events: List) -> str:
        """Determine pod status based on events"""
        if not events:
            return "Unknown"
        
        # Check for error events first
        for event in events:
            # Safely get attributes using getattr with default values
            title = str(event.get('title', '')).lower()
            text = str(event.get('text', '')).lower()
            
            # Handle alert_type specially as it might be an enum
            alert_type = str(event.get('alert_type', 'info')).lower()
            
            # Check for error indicators
            if ('error' in text or 'failed' in text or 'failure' in text or 'exception' in text or
                'error' in title or 'failed' in title or 'failure' in title or
                'exception' in title or 'traceback' in text):
                return "Error"
        
        # Check for warning events
        for event in events:
            # Safely get attributes using getattr with default values
            title = str(event.get('title', '')).lower()
            text = str(event.get('text', '')).lower()
            
            # Handle alert_type specially as it might be an enum
            alert_type = str(event.get('alert_type', 'info')).lower()
            
            # Check for warning indicators
            if ('warn' in text or 'warning' in text or
                'warn' in title or 'warning' in title or
                alert_type == 'warning'):
                return "Warning"
        
        # Check for completion events
        for event in events:
            # Safely get attributes using getattr with default values
            title = str(event.get('title', '')).lower()
            text = str(event.get('text', '')).lower()
            
            # Check for completion indicators
            if ('complete' in text or 'completed' in text or 'success' in text or
                'complete' in title or 'completed' in title or 'success' in title):
                return "Completed"
        
        # Check for running events
        for event in events:
            # Safely get attributes using getattr with default values
            title = str(event.get('title', '')).lower()
            text = str(event.get('text', '')).lower()
            
            # Check for running indicators
            if ('running' in text or 'started' in text or
                'running' in title or 'started' in title):
                return "Running"
        
        # If we have events but couldn't determine a specific status, default to "Running"
        return "Running"

    def _determine_pod_status_from_metrics(self, metrics: Dict) -> str:
        """Determine pod status from metrics data"""
        if not metrics:
            return 'Unknown'

        # First check pod status from Kubernetes state metrics
        if 'pod_status' in metrics:
            status = metrics['pod_status']
            current_phase = status.get('current', 'Unknown')
            
            # Map Kubernetes pod phases to our status
            if current_phase == 'Running':
                # For running pods, check CPU activity
                if 'cpu' in metrics:
                    cpu = metrics['cpu']
                    current_cpu = cpu.get('current', 0)
                    if current_cpu > 1_000_000:  # More than 1m CPU
                        return 'Active'
                    elif cpu.get('max', 0) > 0:
                        return 'Completed'
                return 'Active'
            elif current_phase == 'Succeeded':
                return 'Completed'
            elif current_phase in ['Failed', 'Unknown']:
                return current_phase
            elif current_phase == 'Pending':
                return 'Pending'

        # Fall back to CPU metrics if no pod status
        if 'cpu' in metrics:
            cpu = metrics['cpu']
            current_cpu = cpu.get('current', 0)
            if current_cpu > 1_000_000:  # More than 1m CPU
                return 'Active'
            elif cpu.get('max', 0) > 0:
                return 'Completed'

        # Fall back to memory metrics
        if 'memory' in metrics:
            memory = metrics['memory']
            current_memory = memory.get('current', 0)
            if current_memory > 0:
                return 'Active'

        return 'Unknown'

    def _combine_statuses(self, event_status, metric_status):
        """Combine event and metric status to determine overall pod status"""
        # If we have a definitive status from events, prioritize it
        if event_status in ["Failed", "Error", "Warning"]:
            return event_status
        
        # If metrics indicate the pod is running, but events don't contradict, use that
        if metric_status == "Running" and event_status not in ["Failed", "Error", "Warning"]:
            return "Running"
        
        # If metrics indicate the pod is completed, and events don't contradict, use that
        if metric_status == "Completed" and event_status not in ["Failed", "Error", "Warning"]:
            return "Completed"
        
        # If we have a success event, but metrics are inconclusive
        if event_status == "Success" and metric_status == "Unknown":
            return "Success"
        
        # Default to the metric status if we have it, otherwise use event status
        return metric_status if metric_status != "Unknown" else event_status

    def _print_table(self, headers, rows):
        """Helper method to print a formatted table with the given headers and rows"""
        if not rows:
            print("No data to display")
            return
            
        # Calculate column widths
        col_widths = [20, 10, 82, 10]  # Default widths for timestamp, type, title, status
        
        # Use double-line borders for better readability
        print("╔" + "═" * col_widths[0] + "╦" + "═" * col_widths[1] + "╦" + "═" * col_widths[2] + "╦" + "═" * col_widths[3] + "╗")
        print(f"║ {headers[0]:<{col_widths[0]-2}} ║ {headers[1]:<{col_widths[1]-2}} ║ {headers[2]:<{col_widths[2]-2}} ║ {headers[3]:<{col_widths[3]-2}} ║")
        print("╠" + "═" * col_widths[0] + "╬" + "═" * col_widths[1] + "╬" + "═" * col_widths[2] + "╬" + "═" * col_widths[3] + "╣")

        for row in rows:
            print(f"║ {row[0]:<{col_widths[0]-2}} ║ {row[1]:<{col_widths[1]-2}} ║ {row[2]:<{col_widths[2]-2}} ║ {row[3]:<{col_widths[3]-2}} ║")

        print("╚" + "═" * col_widths[0] + "╩" + "═" * col_widths[1] + "╩" + "═" * col_widths[2] + "╩" + "═" * col_widths[3] + "╝")

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
            current_millicores = cpu['current'] / 1_000_000
            peak_millicores = cpu['max'] / 1_000_000
            avg_millicores = cpu['avg'] / 1_000_000

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
                    'message': f'No CPU limits set. Current usage: {current_millicores:.1f}m (peak: {peak_millicores:.1f}m, avg: {avg_millicores:.1f}m). Consider setting limits based on peak usage.'
                })
            else:
                # Calculate utilization with safety checks
                limit_mcores = cpu_limit / 1_000_000
                if limit_mcores > 0:
                    util_current = min((current_millicores / limit_mcores) * 100, 100)
                    util_peak = min((peak_millicores / limit_mcores) * 100, 100)

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
    parser = argparse.ArgumentParser(description="Diagnose Kubernetes pod metrics and events")
    parser.add_argument("--cluster", help="Kubernetes cluster name", required=True)
    parser.add_argument("--namespace", help="Kubernetes namespace", required=True)
    parser.add_argument("--pod-pattern", dest="pod_pattern", help="Pod name pattern to match", required=False)
    parser.add_argument("--pod", help="Specific pod name to diagnose", required=False)
    parser.add_argument("--hours", type=int, default=1, help="Hours of history to analyze")
    parser.add_argument("--start-time", dest="start_time", help="Start time (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--end-time", dest="end_time", help="End time (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Parse time arguments if provided
    start_time = None
    end_time = None

    if args.start_time:
        try:
            start_time = datetime.strptime(args.start_time, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            print(f"Invalid start time format: {args.start_time}. Expected format: YYYY-MM-DD HH:MM:SS")
            sys.exit(1)

    if args.end_time:
        try:
            end_time = datetime.strptime(args.end_time, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            print(f"Invalid end time format: {args.end_time}. Expected format: YYYY-MM-DD HH:MM:SS")
            sys.exit(1)

    # Set default time window if not specified
    hours = args.hours
    if not start_time and not end_time:
        now = datetime.now()
        end_time = now
        start_time = now - timedelta(hours=hours)

    diagnostics = PodDiagnostics()
    diagnostics.cluster = args.cluster
    diagnostics.namespace = args.namespace

    # First find and select the pod
    if args.pod:
        selected_pod = args.pod
    else:
        selected_pod = diagnostics.select_pod(args.cluster, args.namespace, args.pod_pattern, hours, start_time, end_time)

    if selected_pod:
        # Run diagnostics on the selected pod
        diagnostics.diagnose_pod(args.cluster, args.namespace, selected_pod, hours, start_time, end_time)
    else:
        print("No pod selected for diagnostics")

if __name__ == "__main__":
    main()
