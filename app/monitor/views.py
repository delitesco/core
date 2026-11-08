from app.monitor.base import monitor_bp


@monitor_bp.route("/live")
def live():
    return "live"
