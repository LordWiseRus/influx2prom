#!/usr/bin/env python3
import argparse
import csv
import io
import sys
import json
import requests
import datetime
from src.influx2promql import influx_to_promql


def format_timestamp(timestamp):
    """Convert InfluxDB timestamp to Prometheus timestamp (milliseconds)"""
    try:
        # Try parsing as RFC3339
        dt = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        return str(int(dt.timestamp() * 1000))
    except ValueError:
        # If timestamp is already in epoch format (ms or s)
        if len(timestamp) > 12:  # Likely milliseconds
            return timestamp
        else:  # Likely seconds
            return str(int(float(timestamp) * 1000))


def query_influxdb(url, token, org, query):
    """Query InfluxDB using the Flux API"""
    headers = {
        'Authorization': f'Token {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/csv'
    }
    
    params = {
        'org': org,
        'q': query
    }
    
    response = requests.post(f"{url}/api/v2/query", headers=headers, params=params)
    if response.status_code != 200:
        raise Exception(f"Query failed with status code {response.status_code}: {response.text}")
    
    return response.text


def parse_influxdb_csv(csv_data):
    """Parse InfluxDB CSV data into structured data"""
    parsed_data = []
    
    # Parse CSV
    reader = csv.reader(io.StringIO(csv_data))
    
    # Skip comment lines and find headers
    headers = None
    for row in reader:
        if not row:
            continue
        if row[0].startswith('#'):
            continue
        if not headers:
            headers = row
            break
    
    if not headers:
        raise ValueError("Could not find headers in CSV data")
    
    # Find important column indices
    time_idx = headers.index('_time') if '_time' in headers else None
    measurement_idx = headers.index('_measurement') if '_measurement' in headers else None
    field_idx = headers.index('_field') if '_field' in headers else None
    value_idx = headers.index('_value') if '_value' in headers else None
    
    if None in (time_idx, measurement_idx, field_idx, value_idx):
        raise ValueError("CSV data doesn't contain required columns (_time, _measurement, _field, _value)")
    
    # Process each data row
    for row in reader:
        if not row or len(row) < max(time_idx, measurement_idx, field_idx, value_idx) + 1:
            continue  # Skip incomplete rows
            
        # Extract base metric info
        timestamp = row[time_idx]
        measurement = row[measurement_idx]
        field = row[field_idx]
        value = row[value_idx]
        
        # Extract tags
        tags = {}
        for i, header in enumerate(headers):
            if not header.startswith('_') and i < len(row) and row[i]:
                tags[header] = row[i]
        
        # Store parsed data
        parsed_data.append({
            'timestamp': timestamp,
            'measurement': measurement,
            'field': field,
            'value': value,
            'tags': tags
        })
    
    return parsed_data


def influx_to_prometheus(csv_data):
    """Convert InfluxDB CSV data to Prometheus format"""
    prometheus_lines = []
    
    # Parse InfluxDB data
    parsed_data = parse_influxdb_csv(csv_data)
    
    # Convert to Prometheus format
    for item in parsed_data:
        measurement = item['measurement']
        field = item['field']
        value = item['value']
        timestamp = format_timestamp(item['timestamp'])
        tags = item['tags']
        
        # Create the metric name
        metric_name = f"{measurement}_{field}"
        
        # Format tags as Prometheus labels
        labels = []
        for k, v in tags.items():
            labels.append(f'{k}="{v}"')
        
        labels_str = '{' + ','.join(labels) + '}' if labels else ''
        
        # Format the Prometheus line
        prom_line = f"{metric_name}{labels_str} {value} {timestamp}"
        prometheus_lines.append(prom_line)
    
    return '\n'.join(prometheus_lines)




def main():
    parser = argparse.ArgumentParser(description='Convert InfluxDB data to Prometheus format, or translate InfluxDB queries to PromQL')

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Convert command - simplified functionality (removed Grafana format)
    convert_parser = subparsers.add_parser('convert', help='Convert InfluxDB data to Prometheus format')
    convert_parser.add_argument('--url', required=True, help='InfluxDB URL (e.g., http://localhost:8086)')
    convert_parser.add_argument('--token', required=True, help='InfluxDB API token')
    convert_parser.add_argument('--org', required=True, help='InfluxDB organization')
    convert_parser.add_argument('--query', help='Flux query to execute')
    convert_parser.add_argument('--query-file', help='File containing a Flux query')
    convert_parser.add_argument('--input-json', help='Read data from a JSON file instead of querying InfluxDB')
    convert_parser.add_argument('--output', help='Output file (default: stdout)')

    # Translate command - new functionality for translating queries to PromQL
    translate_parser = subparsers.add_parser('translate', help='Translate InfluxDB queries to PromQL')
    translate_parser.add_argument('--query', help='InfluxDB query to translate')
    translate_parser.add_argument('--query-file', help='File containing an InfluxDB query')
    translate_parser.add_argument('--type', choices=['flux', 'influxql'],
                            help='Force query type interpretation (flux or influxql)')
    translate_parser.add_argument('--output', help='Output file (default: stdout)')

    # For backward compatibility, assume 'convert' command if no command is specified
    args = parser.parse_args()
    if not args.command:
        # Check if we have the arguments for the convert command
        if hasattr(args, 'url') and hasattr(args, 'token') and hasattr(args, 'org') or hasattr(args, 'input_json'):
            args.command = 'convert'
        else:
            parser.print_help()
            sys.exit(1)

    try:
        # Handle different commands
        if args.command == 'translate':
            # Translation mode (InfluxDB query to PromQL)
            if args.query:
                query = args.query
            elif args.query_file:
                with open(args.query_file, 'r') as f:
                    query = f.read()
            else:
                parser.error('Either --query or --query-file must be provided for translate command')

            # Translate to PromQL
            promql = influx_to_promql(query, args.type)

            # Output results
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(promql)
            else:
                print(promql)

        elif args.command == 'convert':
            # Convert mode (simplified functionality)
            # Get the data
            if args.input_json:
                with open(args.input_json, 'r') as f:
                    # Assuming JSON contains a 'csv_data' field
                    input_data = json.load(f)
                    if isinstance(input_data, dict) and 'csv_data' in input_data:
                        csv_data = input_data['csv_data']
                    else:
                        raise ValueError("Input JSON must contain a 'csv_data' field")
            else:
                # Get the query
                if args.query:
                    query = args.query
                elif args.query_file:
                    with open(args.query_file, 'r') as f:
                        query = f.read()
                else:
                    parser.error('Either --query, --query-file, or --input-json must be provided')

                # Query InfluxDB
                csv_data = query_influxdb(args.url, args.token, args.org, query)

            # Convert data to Prometheus format
            output_data = influx_to_prometheus(csv_data)

            # Output results
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(output_data)
            else:
                print(output_data)
                
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()