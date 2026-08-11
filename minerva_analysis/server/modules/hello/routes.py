from flask import Blueprint, jsonify

from minerva_analysis import app as core_app
from minerva_analysis.server.models import data_model

hello_bp = Blueprint('hello', __name__)


@hello_bp.route('/hello_ping', methods=['GET'])
def hello_ping():
    """Proves this module can read core state through data_model's read
    accessors (Phase 1) without importing anything gating-specific."""
    datasource = data_model.get_current_datasource_name()
    return jsonify(
        message=f"Hello from the hello module (datasource: {datasource or 'none loaded'})",
        active_module=core_app.config.get('MINERVA_ACTIVE_MODULE'),
    )
