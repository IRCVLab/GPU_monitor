import logging
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request

logger = logging.getLogger(__name__)


# ─── Query Helper Functions ──────────────────────────────────────────────────

def query_summary(db, start_date: str, end_date: str) -> Dict[str, Any]:
    """
    Query overall statistics summary.

    Args:
        db: DatabaseManager instance
        start_date: ISO format datetime string
        end_date: ISO format datetime string

    Returns:
        Dict with total_gpu_hours, avg_utilization, top_users, server_availability
    """
    with db.get_connection() as conn:
        # Calculate total GPU hours (sum of all GPU utilization over time)
        cursor = conn.execute(
            """
            SELECT
                COUNT(*) as total_records,
                AVG(avg_utilization) as avg_util,
                COUNT(DISTINCT server_alias) as servers_active
            FROM server_summary_metrics
            WHERE timestamp BETWEEN ? AND ?
            """,
            (start_date, end_date),
        )
        row = cursor.fetchone()
        total_records = row['total_records'] if row else 0
        avg_utilization = row['avg_util'] if row else 0
        servers_active = row['servers_active'] if row else 0

        # Calculate GPU hours (records * interval / 60)
        # Assuming 1-minute intervals
        total_gpu_hours = (total_records * servers_active) / 60.0

        # Get top users
        cursor = conn.execute(
            """
            SELECT
                username,
                COUNT(*) as usage_count,
                COUNT(DISTINCT server_alias) as servers_used
            FROM gpu_process_metrics
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY username
            ORDER BY usage_count DESC
            LIMIT 10
            """,
            (start_date, end_date),
        )
        top_users = [dict(row) for row in cursor.fetchall()]

        # Get active user count
        cursor = conn.execute(
            """
            SELECT COUNT(DISTINCT username) as user_count
            FROM gpu_process_metrics
            WHERE timestamp BETWEEN ? AND ?
            """,
            (start_date, end_date),
        )
        user_count = cursor.fetchone()['user_count']

        # Find peak hour
        cursor = conn.execute(
            """
            SELECT
                strftime('%H', timestamp) as hour,
                AVG(avg_utilization) as avg_util
            FROM server_summary_metrics
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY hour
            ORDER BY avg_util DESC
            LIMIT 1
            """,
            (start_date, end_date),
        )
        peak_row = cursor.fetchone()
        peak_hour = f"{peak_row['hour']}:00" if peak_row else "N/A"

        return {
            'total_gpu_hours': round(total_gpu_hours, 2),
            'avg_utilization': round(avg_utilization, 2) if avg_utilization else 0,
            'active_users': user_count,
            'top_users': top_users,
            'peak_hour': peak_hour,
            'servers_active': servers_active,
        }


def query_timeseries(
    db,
    start_date: str,
    end_date: str,
    servers: Optional[List[str]] = None,
    metric: str = 'avg_utilization',
    granularity: str = 'raw',
) -> List[Dict[str, Any]]:
    """
    Query time-series data for charts.

    Args:
        db: DatabaseManager instance
        start_date: ISO format datetime string
        end_date: ISO format datetime string
        servers: List of server aliases (None = all servers)
        metric: Metric name to query
        granularity: 'raw', 'hourly', or 'daily'

    Returns:
        List of {timestamp, server_alias, metric_value}
    """
    # Map metric names to SQL columns
    metric_column_map = {
        'avg_utilization': 'avg_utilization',
        'memory_percent': 'total_memory_used / total_memory * 100',
        'temperature': 'avg_temperature',
    }

    metric_column = metric_column_map.get(metric, 'avg_utilization')

    # Build SQL query based on granularity
    if granularity == 'hourly':
        sql = f"""
            SELECT
                strftime('%Y-%m-%d %H:00:00', timestamp) as timestamp,
                server_alias,
                AVG({metric_column}) as metric_value
            FROM server_summary_metrics
            WHERE timestamp BETWEEN ? AND ?
        """
    elif granularity == 'daily':
        sql = f"""
            SELECT
                strftime('%Y-%m-%d 00:00:00', timestamp) as timestamp,
                server_alias,
                AVG({metric_column}) as metric_value
            FROM server_summary_metrics
            WHERE timestamp BETWEEN ? AND ?
        """
    else:  # raw
        sql = f"""
            SELECT
                timestamp,
                server_alias,
                {metric_column} as metric_value
            FROM server_summary_metrics
            WHERE timestamp BETWEEN ? AND ?
        """

    params = [start_date, end_date]

    # Add server filter
    if servers and 'All' not in servers:
        placeholders = ','.join('?' * len(servers))
        sql += f" AND server_alias IN ({placeholders})"
        params.extend(servers)

    # Group by for aggregated granularities
    if granularity in ['hourly', 'daily']:
        sql += " GROUP BY timestamp, server_alias"

    sql += " ORDER BY timestamp, server_alias LIMIT 10000"

    with db.get_connection() as conn:
        cursor = conn.execute(sql, params)
        results = [dict(row) for row in cursor.fetchall()]

    return results


def query_users(
    db, start_date: str, end_date: str, servers: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Query per-user GPU usage statistics.

    Args:
        db: DatabaseManager instance
        start_date: ISO format datetime string
        end_date: ISO format datetime string
        servers: List of server aliases (None = all servers)

    Returns:
        List of {username, total_gpu_hours, servers_used, avg_memory_mb}
    """
    sql = """
        SELECT
            username,
            COUNT(*) * 1.0 / 60 as total_gpu_hours,
            COUNT(DISTINCT server_alias) as servers_used,
            AVG(memory_used) as avg_memory_mb
        FROM gpu_process_metrics
        WHERE timestamp BETWEEN ? AND ?
    """

    params = [start_date, end_date]

    if servers and 'All' not in servers:
        placeholders = ','.join('?' * len(servers))
        sql += f" AND server_alias IN ({placeholders})"
        params.extend(servers)

    sql += " GROUP BY username ORDER BY total_gpu_hours DESC LIMIT 100"

    with db.get_connection() as conn:
        cursor = conn.execute(sql, params)
        results = []
        for row in cursor.fetchall():
            results.append({
                'username': row['username'],
                'total_gpu_hours': round(row['total_gpu_hours'], 2),
                'servers_used': row['servers_used'],
                'avg_memory_mb': round(row['avg_memory_mb'], 2) if row['avg_memory_mb'] else 0,
            })

    return results


def query_heatmap(db, start_date: str, end_date: str) -> Dict[str, Any]:
    """
    Query server usage heatmap data (servers × hours).

    Args:
        db: DatabaseManager instance
        start_date: ISO format datetime string
        end_date: ISO format datetime string

    Returns:
        {servers: [...], hours: [0-23], data: [[utilization_matrix]]}
    """
    sql = """
        SELECT
            server_alias,
            CAST(strftime('%H', timestamp) AS INTEGER) as hour,
            AVG(avg_utilization) as avg_util
        FROM server_summary_metrics
        WHERE timestamp BETWEEN ? AND ?
        GROUP BY server_alias, hour
        ORDER BY server_alias, hour
    """

    with db.get_connection() as conn:
        cursor = conn.execute(sql, (start_date, end_date))
        rows = cursor.fetchall()

    # Build heatmap data structure
    servers = sorted(set(row['server_alias'] for row in rows))
    hours = list(range(24))

    # Initialize matrix
    data_matrix = {server: {hour: 0.0 for hour in hours} for server in servers}

    # Fill matrix
    for row in rows:
        server = row['server_alias']
        hour = row['hour']
        util = row['avg_util'] or 0.0
        data_matrix[server][hour] = round(util, 2)

    # Convert to list format for frontend
    heatmap_data = []
    for server in servers:
        for hour in hours:
            heatmap_data.append({
                'server': server,
                'hour': hour,
                'utilization': data_matrix[server][hour],
            })

    return {
        'servers': servers,
        'hours': hours,
        'data': heatmap_data,
    }


def query_power_stats(
    db,
    start_date: str,
    end_date: str,
    servers: Optional[List[str]] = None,
    granularity: str = 'raw'
) -> Dict[str, Any]:
    """
    Query power consumption statistics.

    Args:
        db: DatabaseManager instance
        start_date: ISO format datetime string
        end_date: ISO format datetime string
        servers: List of server aliases (None = all servers)
        granularity: 'raw', 'hourly', or 'daily'

    Returns:
        Dict with power time-series and summary statistics
    """
    # Build time-series SQL based on granularity
    if granularity == 'hourly':
        sql = """
            SELECT
                strftime('%Y-%m-%d %H:00:00', timestamp) as timestamp,
                server_alias,
                AVG(power_draw) as avg_power
            FROM gpu_metrics
            WHERE timestamp BETWEEN ? AND ? AND power_draw IS NOT NULL
        """
    elif granularity == 'daily':
        sql = """
            SELECT
                strftime('%Y-%m-%d 00:00:00', timestamp) as timestamp,
                server_alias,
                AVG(power_draw) as avg_power
            FROM gpu_metrics
            WHERE timestamp BETWEEN ? AND ? AND power_draw IS NOT NULL
        """
    else:  # raw
        sql = """
            SELECT
                timestamp,
                server_alias,
                power_draw as avg_power
            FROM gpu_metrics
            WHERE timestamp BETWEEN ? AND ? AND power_draw IS NOT NULL
        """

    params = [start_date, end_date]

    # Add server filter
    if servers and 'All' not in servers:
        placeholders = ','.join('?' * len(servers))
        sql += f" AND server_alias IN ({placeholders})"
        params.extend(servers)

    # Group by for aggregated granularities
    if granularity in ['hourly', 'daily']:
        sql += " GROUP BY timestamp, server_alias"

    sql += " ORDER BY timestamp, server_alias LIMIT 10000"

    with db.get_connection() as conn:
        cursor = conn.execute(sql, params)
        timeseries = [dict(row) for row in cursor.fetchall()]

        # Get summary statistics
        summary_sql = """
            SELECT
                server_alias,
                COUNT(*) as records,
                AVG(power_draw) as avg_power,
                MAX(power_draw) as max_power,
                MIN(power_draw) as min_power
            FROM gpu_metrics
            WHERE timestamp BETWEEN ? AND ? AND power_draw IS NOT NULL
        """

        summary_params = [start_date, end_date]

        if servers and 'All' not in servers:
            placeholders = ','.join('?' * len(servers))
            summary_sql += f" AND server_alias IN ({placeholders})"
            summary_params.extend(servers)

        summary_sql += " GROUP BY server_alias"

        cursor = conn.execute(summary_sql, summary_params)
        summary = [dict(row) for row in cursor.fetchall()]

    return {
        'timeseries': timeseries,
        'summary': summary
    }


def query_health(db) -> Dict[str, Any]:
    """
    Query collection service health metrics.

    Args:
        db: DatabaseManager instance

    Returns:
        Health status information
    """
    with db.get_connection() as conn:
        # Get last collection
        cursor = conn.execute(
            """
            SELECT timestamp, collection_duration_ms, servers_collected, errors
            FROM collection_metadata
            ORDER BY timestamp DESC
            LIMIT 1
            """
        )
        last_collection = cursor.fetchone()

        # Get record counts
        record_counts = db.get_record_counts()

        # Get database size
        db_size = db.get_db_size()

        return {
            'last_collection': dict(last_collection) if last_collection else None,
            'record_counts': record_counts,
            'db_size_bytes': db_size,
            'db_size_mb': round(db_size / (1024 * 1024), 2),
        }


# ─── Flask Route Registration ────────────────────────────────────────────────

def register_stats_routes(app: Flask, db):
    """
    Register all statistics API routes to Flask app.

    Args:
        app: Flask application instance
        db: DatabaseManager instance
    """

    @app.route("/api/stats/summary")
    def api_stats_summary():
        """Get overall statistics summary."""
        try:
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')

            # Default to last 7 days if not provided
            if not start_date or not end_date:
                end = datetime.now()
                start = end - timedelta(days=7)
                start_date = start.isoformat()
                end_date = end.isoformat()

            result = query_summary(db, start_date, end_date)
            return jsonify(result)

        except Exception as e:
            logger.error(f"Error in /api/stats/summary: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    @app.route("/api/stats/timeseries")
    def api_stats_timeseries():
        """Get time-series data for charts."""
        try:
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            servers_str = request.args.get('servers')
            metric = request.args.get('metric', 'avg_utilization')
            granularity = request.args.get('granularity', 'raw')

            # Default to last 7 days if not provided
            if not start_date or not end_date:
                end = datetime.now()
                start = end - timedelta(days=7)
                start_date = start.isoformat()
                end_date = end.isoformat()

            # Parse servers list
            servers = None
            if servers_str:
                servers = [s.strip() for s in servers_str.split(',')]

            result = query_timeseries(db, start_date, end_date, servers, metric, granularity)
            return jsonify(result)

        except Exception as e:
            logger.error(f"Error in /api/stats/timeseries: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    @app.route("/api/stats/users")
    def api_stats_users():
        """Get per-user GPU usage statistics."""
        try:
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            servers_str = request.args.get('servers')

            # Default to last 7 days if not provided
            if not start_date or not end_date:
                end = datetime.now()
                start = end - timedelta(days=7)
                start_date = start.isoformat()
                end_date = end.isoformat()

            # Parse servers list
            servers = None
            if servers_str:
                servers = [s.strip() for s in servers_str.split(',')]

            result = query_users(db, start_date, end_date, servers)
            return jsonify(result)

        except Exception as e:
            logger.error(f"Error in /api/stats/users: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    @app.route("/api/stats/heatmap")
    def api_stats_heatmap():
        """Get server usage heatmap data."""
        try:
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')

            # Default to last 7 days if not provided
            if not start_date or not end_date:
                end = datetime.now()
                start = end - timedelta(days=7)
                start_date = start.isoformat()
                end_date = end.isoformat()

            result = query_heatmap(db, start_date, end_date)
            return jsonify(result)

        except Exception as e:
            logger.error(f"Error in /api/stats/heatmap: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    @app.route("/api/stats/power")
    def api_stats_power():
        """Get power consumption statistics."""
        try:
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            servers_str = request.args.get('servers')
            granularity = request.args.get('granularity', 'hourly')

            # Default to last 7 days if not provided
            if not start_date or not end_date:
                end = datetime.now()
                start = end - timedelta(days=7)
                start_date = start.isoformat()
                end_date = end.isoformat()

            # Parse servers list
            servers = None
            if servers_str:
                servers = [s.strip() for s in servers_str.split(',')]

            result = query_power_stats(db, start_date, end_date, servers, granularity)
            return jsonify(result)

        except Exception as e:
            logger.error(f"Error in /api/stats/power: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    @app.route("/api/stats/health")
    def api_stats_health():
        """Get collection service health metrics."""
        try:
            result = query_health(db)
            return jsonify(result)

        except Exception as e:
            logger.error(f"Error in /api/stats/health: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    logger.info("Statistics API routes registered successfully")
