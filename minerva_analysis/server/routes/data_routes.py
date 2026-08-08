from minerva_analysis import app
from flask import make_response, render_template, request, Response, jsonify, abort, send_file, stream_with_context
import io
from PIL import Image
from minerva_analysis import data_path, get_config
from minerva_analysis.server.models import data_model
from pathlib import Path
from time import time
import polars as pl
import gzip
import json
import orjson
import threading
from collections import OrderedDict
from os import walk
from flask_sqlalchemy import SQLAlchemy


@app.route('/init_database', methods=['GET'])
def init_database():
    datasource = request.args.get('datasource')
    data_model.init(datasource)
    resp = jsonify(success=True)
    return resp


@app.route('/config')
def serve_config():
    return get_config()


@app.route('/get_nearest_cell', methods=['GET'])
def get_nearest_cell():
    x = float(request.args.get('point_x'))
    y = float(request.args.get('point_y'))
    datasource = request.args.get('datasource')
    resp = data_model.query_for_closest_cell(x, y, datasource)
    return serialize_and_submit_json(resp)


# Gets a row based on the index
@app.route('/get_database_row', methods=['GET'])
def get_database_row():
    datasource = request.args.get('datasource')
    row = int(request.args.get('row'))
    resp = data_model.get_row(row, datasource)
    return serialize_and_submit_json(resp)


@app.route('/get_channel_names', methods=['GET'])
def get_channel_names():
    datasource = request.args.get('datasource')
    shortnames = bool(request.args.get('shortNames'))
    resp = data_model.get_channel_names(datasource, shortnames)
    return serialize_and_submit_json(resp)


@app.route('/get_all_cells/<dtype>/', methods=['GET'])
def get_all_cells(dtype):
    datasource = request.args.get('datasource')
    data_type = int if 'integer' == dtype else float
    start_keys = list(request.args.get('start_keys').split(','))
    resp = data_model.get_all_cells(datasource, start_keys, data_type)
    content = gzip.compress(resp.tobytes('C'))
    response = make_response(content)
    response.headers.set('Content-Type', 'application/octet-stream')
    response.headers['Content-length'] = len(content)
    response.headers['Content-Encoding'] = 'gzip'
    return response


@app.route('/get_centroid_manifest', methods=['GET'])
def get_centroid_manifest():
    datasource = request.args.get('datasource')
    resp = data_model.get_centroid_manifest(datasource)
    return serialize_and_submit_json(resp)


@app.route('/get_centroid_tiles', methods=['POST'])
def get_centroid_tiles():
    post_data = json.loads(request.data)
    datasource = post_data['datasource']
    level = int(post_data.get('level', 0))
    tiles = post_data.get('tiles', [])
    filter = post_data.get('filter', {})
    max_points = post_data.get('max_points')
    resp = data_model.get_centroid_tiles(datasource, level, tiles, filter, max_points)
    content = gzip.compress(resp.tobytes('C'))
    response = make_response(content)
    response.headers.set('Content-Type', 'application/octet-stream')
    response.headers['Content-length'] = len(content)
    response.headers['Content-Encoding'] = 'gzip'
    response.headers['X-Centroid-Record-Count'] = len(resp)
    return response


@app.route('/get_gated_cell_ids', methods=['GET'])
def get_gated_cell_ids():
    datasource = request.args.get('datasource')
    filter = json.loads(request.args.get('filter'))
    start_keys = list(request.args.get('start_keys').split(','))
    resp = data_model.get_gated_cells(datasource, filter, start_keys)
    return serialize_and_submit_json(resp)


@app.route('/get_gated_cell_ids_custom', methods=['GET'])
def get_gated_cell_ids_custom():
    datasource = request.args.get('datasource')
    filter = json.loads(request.args.get('filter'))
    start_keys = list(request.args.get('start_keys').split(','))
    resp = data_model.get_gated_cells_custom(datasource, filter, start_keys)
    return serialize_and_submit_json(resp)

@app.route('/get_channel_cell_ids', methods=['GET'])
def get_channel_cell_ids():
    datasource = request.args.get('datasource')
    filter = json.loads(request.args.get('filter'))
    resp = data_model.get_channel_cells(datasource, filter)
    return serialize_and_submit_json(resp)


@app.route('/get_database_description', methods=['GET'])
def get_database_description():
    datasource = request.args.get('datasource')
    resp = data_model.get_datasource_description(datasource)
    return serialize_and_submit_json(resp)


@app.route('/get_channel_gmm', methods=['GET'])
def get_channel_gmm():
    channel = request.args.get('channel')
    datasource = request.args.get('datasource')
    resp = data_model.get_channel_gmm(channel, datasource)
    return serialize_and_submit_json(resp)

@app.route('/get_gating_gmm', methods=['POST'])
def get_gating_gmm():
    post_data = json.loads(request.data)
    channel = post_data['channel']
    datasource = post_data['datasource']
    selection_ids = post_data['selection_ids']
    resp = data_model.get_gating_gmm(channel, datasource, selection_ids)
    return serialize_and_submit_json(resp)

@app.route('/upload_gates', methods=['POST'])
def upload_gates():
    file = request.files['file']
    if file.filename.endswith('.csv') == False:
        abort(422)
    datasource = request.form['datasource']
    save_path = data_path / datasource
    if save_path.is_dir() == False:
        abort(422)

    filename = 'uploaded_gates.csv'
    file.save(Path(save_path / filename))
    resp = jsonify(success=True)
    return resp

@app.route('/upload_channels', methods=['POST'])
def upload_channels():
    file = request.files['file']
    if file.filename.endswith('.csv') == False:
        abort(422)
    datasource = request.form['datasource']
    save_path = data_path / datasource
    if save_path.is_dir() == False:
        abort(422)

    filename = 'uploaded_channels.csv'
    file.save(Path(save_path / filename))
    resp = jsonify(success=True)
    return resp

@app.route('/get_ome_metadata', methods=['GET'])
def get_ome_metadata():
    datasource = request.args.get('datasource')
    resp = data_model.get_ome_metadata(datasource)
    if hasattr(resp, "model_dump"):
        resp = resp.model_dump(mode="json")
    elif hasattr(resp, "dict"):
        resp = resp.dict()
    elif not resp:
        resp = {}
    # OME-Types handles jsonify itself, so skip the orjson conversion
    response = app.response_class(
        response=json.dumps(resp),
        mimetype='application/json'
    )
    return response


@app.route('/download_gating_csv', methods=['POST'])
def download_gating_csv():
    datasource = request.form['datasource']
    filename = request.form['filename']

    filter = json.loads(request.form['filter'])
    channels = json.loads(request.form['channels'])
    lassos = json.loads(request.form['lassos'])
    selection_ids = json.loads(request.form['selection_ids'])
    fullCsv = json.loads(request.form['fullCsv'])
    encoding = request.form['encoding']
    if fullCsv:
        csv = data_model.download_gating_csv(datasource, filter, channels, selection_ids, encoding)
        return Response(
            stream_with_context(_stream_csv(csv)),
            mimetype="text/csv",
            headers={"Content-disposition":
                         "attachment; filename=" + filename + ".csv"})
    else:
        csv = data_model.download_gates(datasource, filter, channels, lassos)
        return Response(
            csv.write_csv(),
            mimetype="text/csv",
            headers={"Content-disposition":
                         "attachment; filename=" + filename + ".csv"})

@app.route('/save_gating_list', methods=['POST'])
def save_gating_list():
    post_data = json.loads(request.data)

    datasource = post_data['datasource']
    filter = post_data['filter']
    channels = post_data['channels']
    lassos = post_data['lassos']

    data_model.save_gating_list(datasource, filter, channels, lassos)

    resp = jsonify(success=True)
    return resp

@app.route('/get_saved_gating_list', methods=['GET'])
def get_saved_gating_list():
    datasource = request.args.get('datasource')
    resp = data_model.get_saved_gating_list(datasource)
    return serialize_and_submit_json(resp)

@app.route('/download_channels_csv', methods=['POST'])
def download_channels_csv():
    filename = request.form['filename']

    datasource = request.form['datasource']
    map_channels = json.loads(request.form['map_channels'])
    active_channels = json.loads(request.form['active_channels'])
    list_colors = json.loads(request.form['list_colors'])
    list_ranges = json.loads(request.form['list_ranges'])
    list_channels = json.loads(request.form['list_channels'])
    csv = data_model.download_channels(datasource, map_channels, active_channels, list_colors, list_ranges, list_channels)
    return Response(
        csv.write_csv(),
        mimetype="text/csv",
        headers={"Content-disposition":
                     "attachment; filename=" + filename + ".csv"})

@app.route('/save_channel_list', methods=['POST'])
def save_channel_list():
    post_data = json.loads(request.data)

    datasource = post_data['datasource']
    map_channels = post_data['map_channels']
    active_channels = post_data['active_channels']
    list_colors = post_data['list_colors']
    list_ranges = post_data['list_ranges']
    list_channels = post_data['list_channels']

    data_model.save_channel_list(datasource, map_channels, active_channels, list_colors, list_ranges, list_channels)

    resp = jsonify(success=True)
    return resp

@app.route('/get_uploaded_gating_csv_values', methods=['GET'])
def get_gating_csv_values():
    datasource = request.args.get('datasource')
    file_path = data_path / datasource / 'uploaded_gates.csv'
    if file_path.is_file() == False:
        abort(422)
    csv = pl.read_csv(file_path)
    obj = csv.to_dicts()
    return serialize_and_submit_json(obj)

@app.route('/get_uploaded_channel_csv_values', methods=['GET'])
def get_channel_csv_values():
    datasource = request.args.get('datasource')
    file_path = data_path / datasource / 'uploaded_channels.csv'
    if file_path.is_file() == False:
        abort(422)
    csv = pl.read_csv(file_path)
    obj = csv.to_dicts()
    return serialize_and_submit_json(obj)

@app.route('/get_saved_channel_list', methods=['GET'])
def get_saved_channel_list():
    datasource = request.args.get('datasource')
    resp = data_model.get_saved_channel_list(datasource)
    return serialize_and_submit_json(resp)

_tile_png_cache = OrderedDict()
_tile_png_cache_lock = threading.Lock()
_TILE_PNG_CACHE_MAX = 1500


def _get_tile_png_bytes(datasource, channel, level, tile):
    # Keyed on data_model.load_generation so a datasource reload (which may
    # regenerate segmentation, per ensure_outline_segmentation) naturally
    # invalidates previously cached tiles without cross-module cache access.
    key = (data_model.load_generation, datasource, channel, level, tile)
    with _tile_png_cache_lock:
        cached = _tile_png_cache.get(key)
        if cached is not None:
            _tile_png_cache.move_to_end(key)
            return cached

    png = data_model.generate_zarr_png(datasource, channel, level, tile)
    file_object = io.BytesIO()
    Image.fromarray(png).save(file_object, 'PNG', compress_level=0)
    encoded = file_object.getvalue()

    with _tile_png_cache_lock:
        _tile_png_cache[key] = encoded
        _tile_png_cache.move_to_end(key)
        while len(_tile_png_cache) > _TILE_PNG_CACHE_MAX:
            _tile_png_cache.popitem(last=False)
    return encoded


# E.G /generated/data/melanoma/channel_00_files/13/16_18.png
@app.route('/generated/data/<string:datasource>/<string:channel>/<string:level>/<string:tile>')
def generate_png(datasource, channel, level, tile):
    encoded = _get_tile_png_bytes(datasource, channel, level, tile)
    return send_file(io.BytesIO(encoded), mimetype='image/PNG')

def _stream_csv(df, chunksize=100_000):
    """Yield a large DataFrame as CSV in row chunks instead of materializing
    the full serialized string (and holding it alongside the DataFrame) in
    memory at once, as df.write_csv() would for a multi-million-row gating
    export. Polars has no built-in chunked-string-generator, so this slices
    and writes each chunk by hand."""
    header = True
    for start in range(0, df.height, chunksize):
        yield df.slice(start, chunksize).write_csv(include_header=header)
        header = False


def serialize_and_submit_json(data):
    response = app.response_class(
        response=orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY),
        mimetype='application/json'
    )
    return response

