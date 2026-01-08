#!/usr/bin/env python3
"""
Module for translating InfluxDB queries (Flux or InfluxQL) to PromQL
"""
import re
import sys


def convert_flux_to_promql(flux_query):
    """
    Convert a Flux query to PromQL
    
    Parameters:
    -----------
    flux_query : str
        The Flux query to convert
        
    Returns:
    --------
    str
        The equivalent PromQL query
    """
    # Basic patterns to identify in Flux queries
    bucket_pattern = r'from\(bucket:\s*"([^"]+)"\)'
    range_pattern = r'range\(start:\s*([^,\)]+)(?:,\s*stop:\s*([^,\)]+))?\)'
    # Support both r.tag and r["tag"] syntax, and both "value" and "${var}" values
    filter_pattern = r'filter\(fn:\s*\(r\)\s*=>\s*r(?:\.|\[")([^\s"\]]+)(?:"\])?\s*==\s*"([^"]+)"\)'
    field_pattern = r'_field\s*==\s*"([^"]+)"'
    measurement_pattern = r'_measurement\s*==\s*"([^"]+)"'
    aggregate_pattern = r'(mean|sum|count|min|max|stddev)\(\)'
    
    # Extract bucket, time range, filters, etc.
    bucket_match = re.search(bucket_pattern, flux_query)
    range_match = re.search(range_pattern, flux_query)
    measurement_match = re.search(measurement_pattern, flux_query)
    field_match = re.search(field_pattern, flux_query)
    aggregate_match = re.search(aggregate_pattern, flux_query)
    
    # Initialize PromQL components
    metric_name = ""
    filters = []
    time_range = ""
    aggregation = ""
    
    # Parse bucket (not directly used in PromQL but useful for context)
    bucket = bucket_match.group(1) if bucket_match else None
    
    # Parse time range
    if range_match:
        start_time = range_match.group(1)
        stop_time = range_match.group(2)
        
        # Convert relative time notation
        if start_time.startswith('-'):
            # PromQL uses different time units notation
            time_range = f"[{start_time.replace('h', 'h').replace('d', 'd').replace('m', 'm').replace('s', 's')}]"
        else:
            # For absolute timestamps, would need more complex conversion
            time_range = ""  # Placeholder for absolute time conversion
    
    # Parse measurement and field to create the metric name
    if measurement_match and field_match:
        measurement = measurement_match.group(1)
        field = field_match.group(1)
        metric_name = f"{measurement}_{field}"
    
    # Look for additional filters
    filter_matches = re.finditer(filter_pattern, flux_query)
    for match in filter_matches:
        tag = match.group(1)
        value = match.group(2)
        if tag not in ["_measurement", "_field"]:  # Skip these as they're part of the metric name
            filters.append(f'{tag}="{value}"')
    
    # Parse aggregation
    if aggregate_match:
        agg_func = aggregate_match.group(1)
        # Map Flux aggregations to PromQL
        agg_map = {
            "mean": "avg",
            "sum": "sum",
            "count": "count",
            "min": "min",
            "max": "max",
            "stddev": "stddev"
        }
        if agg_func in agg_map:
            aggregation = agg_map[agg_func]
    
    # Construct the PromQL query
    promql = metric_name
    
    # Add filters
    if filters:
        promql += "{" + ", ".join(filters) + "}"
    
    # Add time range
    if time_range:
        promql += time_range
    
    # Add aggregation
    if aggregation:
        promql = f"{aggregation}({promql})"
    
    return promql


def convert_influxql_to_promql(influxql_query):
    """
    Convert an InfluxQL query to PromQL
    
    Parameters:
    -----------
    influxql_query : str
        The InfluxQL query to convert
        
    Returns:
    --------
    str
        The equivalent PromQL query
    """
    # Basic patterns to identify in InfluxQL queries
    select_pattern = r'SELECT\s+([^FROM]+)FROM\s+([^\s;]+)'
    where_pattern = r'WHERE\s+(.+?)(?:GROUP BY|ORDER BY|LIMIT|$)'
    time_pattern = r'time\s*(>=|>|<=|<)\s*([^\s)]+)'
    tag_pattern = r'([a-zA-Z0-9_]+)\s*=\s*\'([^\']+)\''
    group_by_pattern = r'GROUP BY\s+(.+?)(?:ORDER BY|LIMIT|$)'
    
    # Extract components of the InfluxQL query
    select_match = re.search(select_pattern, influxql_query, re.IGNORECASE)
    where_match = re.search(where_pattern, influxql_query, re.IGNORECASE)
    group_by_match = re.search(group_by_pattern, influxql_query, re.IGNORECASE)
    
    if not select_match:
        return "Error: Could not parse InfluxQL SELECT statement"
    
    # Parse selection and measurement
    select_clause = select_match.group(1).strip()
    measurement = select_match.group(2).strip()
    
    # Parse functions like mean(), sum(), etc.
    function_pattern = r'([a-zA-Z0-9_]+)\(([^)]*)\)'
    function_match = re.search(function_pattern, select_clause)
    
    metric_name = ""
    promql_function = ""
    
    if function_match:
        func_name = function_match.group(1).lower()
        field = function_match.group(2).strip('"\'')
        
        # Map InfluxQL functions to PromQL
        func_map = {
            "mean": "avg",
            "sum": "sum",
            "count": "count",
            "min": "min",
            "max": "max"
        }
        
        if func_name in func_map:
            promql_function = func_map[func_name]
        
        metric_name = f"{measurement}_{field}"
    else:
        # Simple field selection without aggregation
        field = select_clause.strip('"\'')
        metric_name = f"{measurement}_{field}"
    
    # Parse WHERE clause
    filters = []
    time_range = ""
    
    if where_match:
        where_clause = where_match.group(1).strip()
        
        # Extract time constraints
        time_match = re.search(time_pattern, where_clause, re.IGNORECASE)
        if time_match:
            operator = time_match.group(1)
            time_value = time_match.group(2)
            
            # Handle relative time like "now() - 1h"
            if "now()" in time_value and "-" in time_value:
                duration_match = re.search(r'now\(\)\s*-\s*(\d+)([hmd])', time_value)
                if duration_match:
                    amount = duration_match.group(1)
                    unit = duration_match.group(2)
                    time_range = f"[{amount}{unit}]"
        
        # Extract tag filters
        tag_matches = re.finditer(tag_pattern, where_clause)
        for match in tag_matches:
            tag = match.group(1)
            value = match.group(2)
            filters.append(f'{tag}="{value}"')
    
    # Construct the PromQL query
    promql = metric_name
    
    # Add filters
    if filters:
        promql += "{" + ", ".join(filters) + "}"
    
    # Add time range
    if time_range:
        promql += time_range
    
    # Add aggregation function
    if promql_function:
        promql = f"{promql_function}({promql})"
    
    return promql


def detect_query_type(query):
    """
    Detect whether the input query is Flux or InfluxQL
    
    Parameters:
    -----------
    query : str
        The query to check
        
    Returns:
    --------
    str
        'flux' or 'influxql'
    """
    # Simple heuristic: Flux queries typically start with "from" or have "|>" pipe operators
    if query.strip().lower().startswith('from') or '|>' in query:
        return 'flux'
    # InfluxQL queries typically start with SELECT
    elif query.strip().upper().startswith('SELECT'):
        return 'influxql'
    else:
        return 'unknown'


def influx_to_promql(query, force_type=None):
    """
    Convert an InfluxDB query (auto-detecting type or using specified type) to PromQL
    
    Parameters:
    -----------
    query : str
        The InfluxDB query to convert
    force_type : str, optional
        Force interpretation as 'flux' or 'influxql', by default None (auto-detect)
        
    Returns:
    --------
    str
        The equivalent PromQL query
    """
    query_type = force_type if force_type else detect_query_type(query)
    
    if query_type == 'flux':
        return convert_flux_to_promql(query)
    elif query_type == 'influxql':
        return convert_influxql_to_promql(query)
    else:
        return "Error: Could not determine query type. Please specify using --type"


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Convert InfluxDB queries to PromQL')
    parser.add_argument('--query', help='InfluxDB query to convert')
    parser.add_argument('--query-file', help='File containing an InfluxDB query')
    parser.add_argument('--type', choices=['flux', 'influxql'], help='Force query type interpretation')
    parser.add_argument('--output', help='Output file (default: stdout)')
    
    args = parser.parse_args()
    
    try:
        # Get the query
        if args.query:
            query = args.query
        elif args.query_file:
            with open(args.query_file, 'r') as f:
                query = f.read()
        else:
            parser.error('Either --query or --query-file must be provided')
        
        # Convert to PromQL
        promql = influx_to_promql(query, args.type)
        
        # Output results
        if args.output:
            with open(args.output, 'w') as f:
                f.write(promql)
        else:
            print(promql)
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()