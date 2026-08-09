from sklearn.neighbors import BallTree
from sklearn.preprocessing import MinMaxScaler
import numpy as np
import polars as pl
import polars.selectors as cs
import json
import os
import io
from pathlib import Path
from pathlib import PurePath
from ome_types import from_xml
from minerva_analysis import config_json_path, data_path, cwd_path
from minerva_analysis.server.utils import pyramid_assemble, pyramid_upgrade
from minerva_analysis.server.models import database_model, centroid_tiles
from minerva_analysis.server.utils import smallestenclosingcircle
import matplotlib.path as mpltPath
from itertools import chain
import dateutil.parser
import time
import pickle
import tifffile as tf
import re
import threading
import zarr
import cv2
from sklearn.mixture import GaussianMixture
from scipy.stats import norm
from skimage.measure import block_reduce
from skimage.transform import resize

ball_tree = None
database = None
source = None
config = None
seg = None
zarray = None
channels = None
metadata = None
load_lock = threading.RLock()

# Cache of derived, expensive-to-recompute results, keyed off the currently
# loaded datasource. Cleared whenever load_datasource actually (re)loads data,
# since these caches were only ever valid for the previously loaded content.
_gmm_cache = {}
_description_cache = {}
_gate_filter_cache = {}
# Incremented each time load_datasource actually (re)loads data, so other
# modules can key a cache off "which load is this" without importing this
# module's internal cache dicts directly.
load_generation = 0


def _zarr_level(group, level):
    if isinstance(group, zarr.Array):
        return group
    return group[str(level)]


def _zarr_levels(group):
    if isinstance(group, zarr.Array):
        return [group]
    return [group[str(i)] for i in range(len(group))]


def _sample_segmentation_array(level):
    shape = level.shape[-2:]
    max_sample_dim = 1536
    step = max(1, int(np.ceil(max(shape) / max_sample_dim)))
    sample = level[::step, ::step]
    return np.asarray(sample)


def _looks_like_outline_mask(segmentation_path):
    if str(segmentation_path).endswith('.zarr'):
        group = zarr.open(segmentation_path)
        sample = _sample_segmentation_array(_zarr_levels(group)[0])
    else:
        with tf.TiffFile(str(segmentation_path), is_ome=False) as seg_io:
            group = zarr.open(seg_io.series[0].aszarr())
            sample = _sample_segmentation_array(_zarr_levels(group)[0])
    if sample.ndim != 2 or sample.size == 0:
        return False

    nonzero = sample != 0
    nonzero_count = int(np.count_nonzero(nonzero))
    if nonzero_count == 0:
        return False

    density = nonzero_count / sample.size
    center = nonzero[1:-1, 1:-1]
    if center.size == 0:
        return density < 0.25

    same_id_interior = (
        center
        & (sample[1:-1, 1:-1] == sample[:-2, 1:-1])
        & (sample[1:-1, 1:-1] == sample[2:, 1:-1])
        & (sample[1:-1, 1:-1] == sample[1:-1, :-2])
        & (sample[1:-1, 1:-1] == sample[1:-1, 2:])
    )
    interior_fraction = int(np.count_nonzero(same_id_interior)) / max(1, int(np.count_nonzero(center)))
    return density <= 0.20 and interior_fraction <= 0.05


def _outline_level(labels):
    labels = np.asarray(labels)
    outline = np.zeros(labels.shape, dtype=labels.dtype)
    nonzero = labels != 0
    edge = np.zeros(labels.shape, dtype=bool)
    edge[0, :] = nonzero[0, :]
    edge[-1, :] = nonzero[-1, :]
    edge[:, 0] = edge[:, 0] | nonzero[:, 0]
    edge[:, -1] = edge[:, -1] | nonzero[:, -1]
    edge[1:, :] = edge[1:, :] | (nonzero[1:, :] & (labels[1:, :] != labels[:-1, :]))
    edge[:-1, :] = edge[:-1, :] | (nonzero[:-1, :] & (labels[:-1, :] != labels[1:, :]))
    edge[:, 1:] = edge[:, 1:] | (nonzero[:, 1:] & (labels[:, 1:] != labels[:, :-1]))
    edge[:, :-1] = edge[:, :-1] | (nonzero[:, :-1] & (labels[:, :-1] != labels[:, 1:]))
    outline[edge] = labels[edge]
    return outline


def _downsample_labels_nearest(labels):
    output_shape = tuple(max(1, int(np.ceil(dim / 2))) for dim in labels.shape)
    return resize(
        labels,
        output_shape,
        order=0,
        preserve_range=True,
        anti_aliasing=False,
    ).astype(labels.dtype, copy=False)


def _outline_output_path(segmentation_path, dataDirectory=None):
    source_path = Path(segmentation_path)
    target_dir = Path(dataDirectory) if dataDirectory else source_path.parent
    suffix = ".fast-outlines.pyramid.ome.tiff"
    stem = re.sub(r'\.ome\.tiff|\.ome\.tif|\.tiff|\.tif|\.png|\.zarr', '', source_path.name)
    return target_dir / f"{stem}{suffix}"


def ensure_outline_segmentation(segmentation_path, dataDirectory=None):
    output_path = _outline_output_path(segmentation_path, dataDirectory)
    if _looks_like_outline_mask(segmentation_path):
        return str(segmentation_path)
    if output_path.exists() and _looks_like_outline_mask(output_path):
        return str(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    arrays = []
    if str(segmentation_path).endswith('.zarr'):
        group = zarr.open(segmentation_path)
        levels = _zarr_levels(group)
        if len(levels) > 1:
            arrays = [_outline_level(np.asarray(level)) for level in levels]
        else:
            labels = np.asarray(levels[0])
            while True:
                arrays.append(_outline_level(labels))
                if min(labels.shape) <= 256:
                    break
                labels = _downsample_labels_nearest(labels)
    else:
        with tf.TiffFile(str(segmentation_path), is_ome=False) as seg_io:
            group = zarr.open(seg_io.series[0].aszarr())
            levels = _zarr_levels(group)
            if len(levels) > 1:
                arrays = [_outline_level(np.asarray(level)) for level in levels]
            else:
                labels = np.asarray(levels[0])
                while True:
                    arrays.append(_outline_level(labels))
                    if min(labels.shape) <= 256:
                        break
                    labels = _downsample_labels_nearest(labels)

    with tf.TiffWriter(str(output_path), bigtiff=True) as writer:
        writer.write(
            arrays[0],
            subifds=max(0, len(arrays) - 1),
            photometric='minisblack',
            metadata={'axes': 'YX'},
            compression='zlib',
        )
        for array in arrays[1:]:
            writer.write(
                array,
                subfiletype=1,
                photometric='minisblack',
                compression='zlib',
            )

    return str(output_path)


def init(datasource_name):
    load_ball_tree(datasource_name)


def load_datasource(datasource_name, reload=False):
    global datasource
    global source
    global config
    global seg
    global zarray
    global channels
    global metadata
    global load_generation
    with load_lock:
        if source == datasource_name and datasource is not None and seg is not None and channels is not None and reload is False:
            return
        load_config(datasource_name)
        if reload:
            load_ball_tree(datasource_name, reload=reload)
        csvPath = Path(config[datasource_name]['featureData'][0]['src'])
        print("Loading csv data.. (this can take some time)")
        loaded_datasource = pl.read_csv(csvPath)
        # Manufacture a stable positional 'id' column, mirroring pandas'
        # implicit RangeIndex usage in the code this replaced -- must happen
        # immediately after read_csv, before any other transform, since
        # downstream code treats 'id' as a stable per-row identity.
        loaded_datasource = loaded_datasource.with_row_index("id").with_columns(pl.col("id").cast(pl.Int64))
        numeric_cols = [c for c, dt in loaded_datasource.schema.items() if dt in (pl.Float32, pl.Float64)]
        loaded_datasource = loaded_datasource.with_columns([
            pl.when(pl.col(c) == float("-inf")).then(0).otherwise(pl.col(c)).alias(c)
            for c in numeric_cols
        ])
        print("Loading segmentation.")
        if config[datasource_name]['segmentation'].endswith('.zarr'):
            loaded_seg = zarr.open(config[datasource_name]['segmentation'])
        else:
            seg_io = tf.TiffFile(config[datasource_name]['segmentation'], is_ome=False)
            loaded_seg = zarr.open(seg_io.series[0].aszarr())
        channel_io = tf.TiffFile(config[datasource_name]['channelFile'], is_ome=False)
        print("Loading image descriptions.")
        try:
            xml = channel_io.pages[0].tags['ImageDescription'].value
            loaded_metadata = from_xml(xml).images[0].pixels
        except:
            loaded_metadata = {}
        loaded_channels = zarr.open(channel_io.series[0].aszarr())

        level_series = next(
            level for level in reversed(channel_io.series[0].levels)
            if all(d >= 200 for d in level.shape[1:])
        )
        loaded_zarray = zarr.open(level_series.aszarr())
        if loaded_zarray.shape[1] > 400 or loaded_zarray.shape[2] > 400:
            x_reduce = loaded_zarray.shape[1] // 200
            y_reduce = loaded_zarray.shape[2] // 200
            reduce = np.min([x_reduce, y_reduce])
            loaded_zarray = block_reduce(loaded_zarray, (1, reduce, reduce), np.mean)

        datasource = loaded_datasource
        seg = loaded_seg
        channels = loaded_channels
        zarray = loaded_zarray
        metadata = loaded_metadata
        source = datasource_name
        # Data on disk just changed underneath us (first load or explicit
        # reload) -- any cached GMM/description results are now stale.
        _gmm_cache.clear()
        _description_cache.clear()
        _gate_filter_cache.clear()
        # Bumped so downstream tile-byte caches (keyed on this) know to
        # treat previously cached tiles as stale without needing a direct
        # reference back into this module's caches.
        load_generation += 1
        print("Data loading done.")

    # Warm the description/GMM caches in the background so the first real
    # request after this load doesn't pay for them synchronously.
    threading.Thread(
        target=_warm_datasource_caches, args=(datasource_name,), daemon=True
    ).start()


def load_config(datasource_name):
    global config

    with open(config_json_path, "r+") as configJson:
        config = json.load(configJson)
        updated = False
        # Update Feature SRC
        original = config[datasource_name]['featureData'][0]['src']
        config[datasource_name]['featureData'][0]['src'] = original.replace('static/data', 'minerva_analysis/data')
        csvPath = config[datasource_name]['featureData'][0]['src']
        if Path(csvPath).exists() is False:
            if Path('.' + csvPath).exists():
                csvPath = '.' + csvPath
        config[datasource_name]['featureData'][0]['src'] = str(Path(csvPath))
        if original != config[datasource_name]['featureData'][0]['src']:
            updated = True

        try:
            original = config[datasource_name]['segmentation']
            config[datasource_name]['segmentation'] = original.replace('static/data', 'minerva_analysis/data')
            config[datasource_name]['segmentation'] = ensure_outline_segmentation(
                config[datasource_name]['segmentation'],
                data_path / datasource_name,
            )
            if original != config[datasource_name]['segmentation']:
                updated = True

        except KeyError:
            print(datasource_name, 'is  missing segmentation')

        if updated:
            configJson.seek(0)  # <--- should reset file position to the beginning.
            json.dump(config, configJson, indent=4)
            configJson.truncate()


def _ball_tree_source_signature(csv_path):
    stat = csv_path.stat()
    return {
        "csv_size": stat.st_size,
        "csv_mtime_ns": stat.st_mtime_ns,
    }


def load_ball_tree(datasource_name_name, reload=False):
    global ball_tree
    global datasource
    global config
    if datasource_name_name != source:
        load_datasource(datasource_name_name)

    pickled_kd_tree_path = str(
        PurePath(cwd_path, data_path, datasource_name_name, "ball_tree.pickle"))

    csvPath = Path(config[datasource_name_name]['featureData'][0]['src'])
    signature = _ball_tree_source_signature(csvPath)

    if Path(pickled_kd_tree_path).is_file() and reload is False:
        print("Pickled KD Tree Exists, Loading")
        try:
            with open(pickled_kd_tree_path, "rb") as tree_file:
                cached = pickle.load(tree_file)
            if isinstance(cached, dict) and cached.get('signature') == signature:
                ball_tree = cached['tree']
                print("Pickled KD Tree Loaded.")
                return
            print("Pickled KD Tree is stale (source CSV changed), rebuilding.")
        except Exception as exc:
            print(f"Could not load pickled KD Tree, rebuilding: {exc}")

    print("Creating KD Tree.")
    xCoordinate = config[datasource_name_name]['featureData'][0]['xCoordinate']
    yCoordinate = config[datasource_name_name]['featureData'][0]['yCoordinate']
    # Reuse the feature table load_datasource already parsed instead of
    # re-reading the (potentially multi-million-row) CSV from disk again.
    points = datasource.select([xCoordinate, yCoordinate]).to_numpy()
    ball_tree = BallTree(points, metric='euclidean')
    with open(pickled_kd_tree_path, 'wb') as tree_file:
        pickle.dump({'signature': signature, 'tree': ball_tree}, tree_file)
    print('Creating KD Tree done.')


def _ensure_loaded(datasource_name):
    """Ensure the CSV/BallTree for datasource_name is the currently loaded one."""
    if datasource_name != source:
        load_ball_tree(datasource_name)


_warmup_locks = {}
_warmup_locks_guard = threading.Lock()


def _warmup_lock_for(datasource_name):
    with _warmup_locks_guard:
        if datasource_name not in _warmup_locks:
            _warmup_locks[datasource_name] = threading.Lock()
        return _warmup_locks[datasource_name]


def _warm_datasource_caches(datasource_name):
    """Pre-populate description/GMM caches in the background so the first
    real request after a datasource load doesn't pay for them synchronously.
    Best-effort only: if a concurrent switch to a different datasource races
    this, the _ensure_loaded() calls inside will just reload as needed.
    """
    lock = _warmup_lock_for(datasource_name)
    if not lock.acquire(blocking=False):
        return
    try:
        get_datasource_description(datasource_name)
        for channel in config[datasource_name]['imageData']:
            if channel['name'] != 'Area':
                get_channel_gmm(channel['fullname'], datasource_name)
    except Exception as exc:
        print(f"Background cache warmup failed for {datasource_name}: {exc}")
    finally:
        lock.release()


def query_for_closest_cell(x, y, datasource_name):
    global datasource
    global source
    global ball_tree
    _ensure_loaded(datasource_name)
    distance, index = ball_tree.query([[x, y]], k=1)
    if distance == np.inf:
        return {}
    #         Nothing found
    else:
        try:
            row = datasource[index[0].tolist()]
            obj = row.to_dicts()[0]
            if 'celltype' not in obj:
                obj['celltype'] = ''
            return obj
        except:
            return {}


def get_row(row, datasource_name):
    global database
    global source
    global ball_tree
    _ensure_loaded(datasource_name)
    obj = database.loc[[row]].to_dict(orient='records')[0]
    obj['id'] = row
    return obj


def get_channel_names(datasource_name, shortnames=True):
    global datasource
    global source
    _ensure_loaded(datasource_name)
    if shortnames:
        channel_names = [channel['name'] for channel in config[datasource_name]['imageData'][1:]]
    else:
        channel_names = [channel['fullname'] for channel in config[datasource_name]['imageData'][1:]]
    return channel_names


def _gate_filter_columns(datasource_name, columns):
    """Numeric numpy views of the requested columns pulled from the
    already-loaded datasource, cached (one entry at a time, like
    centroid_tiles._load_filter_table) so repeated gate queries on the same
    columns reuse the same arrays instead of re-deriving them per request.
    """
    key = (datasource_name, tuple(sorted(set(columns))))
    cached = _gate_filter_cache.get(key)
    if cached is not None:
        return cached
    cols = {
        c: datasource[c].cast(pl.Float32, strict=False).fill_null(float('nan')).to_numpy()
        for c in columns
    }
    _gate_filter_cache.clear()
    _gate_filter_cache[key] = cols
    return cols


def _apply_gate_mask(columns, gates, mode='and'):
    n = len(next(iter(columns.values()))) if columns else 0
    keep = np.ones(n, dtype=bool) if mode == 'and' else np.zeros(n, dtype=bool)
    for key, value in gates.items():
        if key not in columns:
            continue
        low, high = float(value[0]), float(value[1])
        match = (columns[key] > low) & (columns[key] < high)
        if mode == 'and':
            keep &= match
        else:
            keep |= match
    return keep


def _records_for_keys(keys, keep):
    arrays = [datasource[k].to_numpy()[keep].tolist() for k in keys]
    return [dict(zip(keys, row)) for row in zip(*arrays)]


def get_channel_cells(datasource_name, channels):
    global datasource

    _ensure_loaded(datasource_name)

    if not channels:
        return []

    gate_range = (0, 65536)
    columns = _gate_filter_columns(datasource_name, channels)
    keep = _apply_gate_mask(columns, {c: gate_range for c in channels}, mode='and')
    ids = datasource['id'].to_numpy()[keep].tolist()
    return [{'id': v} for v in ids]


def get_phenotype_description(datasource):
    try:
        data = ''
        csvPath = config[datasource]['featureData'][0]['celltypeData']
        if Path(csvPath).is_file():
        #old os.path usage: if os.path.isfile(csvPath):
            data = pl.read_csv(csvPath)
            data = data.to_numpy().tolist()
            # data = data.to_json(orient='records', lines=True)
        return data;
    except KeyError:
        return ''
    except TypeError:
        return ''


def get_phenotype_column_name(datasource):
    try:
        return config[datasource]['featureData'][0]['celltype']
    except KeyError:
        return ''
    except TypeError:
        return ''


def get_cells_phenotype(datasource_name):
    global datasource
    global source
    global ball_tree

    range = [0, 65536]

    # Load if not loaded
    _ensure_loaded(datasource_name)

    try:
        phenotype_field = config[datasource_name]['featureData'][0]['celltype']
    except KeyError:
        phenotype_field = 'celltype'
    except TypeError:
        phenotype_field = 'celltype'

    query = datasource.select(['id', phenotype_field]).to_dicts()
    return query


def get_gated_cells(datasource_name, gates, start_keys):
    global datasource

    _ensure_loaded(datasource_name)

    if not gates:
        return []
    columns = _gate_filter_columns(datasource_name, list(gates.keys()))
    keep = _apply_gate_mask(columns, gates, mode='and')
    id_key = start_keys[0]
    values = datasource[id_key].to_numpy()[keep].tolist()
    return [{id_key: v} for v in values]


def get_gated_cells_custom(datasource_name, gates, start_keys):
    global datasource

    _ensure_loaded(datasource_name)

    if not gates:
        return []
    columns = _gate_filter_columns(datasource_name, list(gates.keys()))
    keep = _apply_gate_mask(columns, gates, mode='or')
    query_keys = start_keys + list(gates.keys())
    return _records_for_keys(query_keys, keep)



def get_all_cells(datasource_name, start_keys, data_type=float):
    global datasource
    global source

    # Load if not loaded
    _ensure_loaded(datasource_name)

    query = datasource.select(start_keys).to_numpy().flatten()
    if np.issubdtype(data_type, int):
        return query.astype(np.uint32)
    return query.astype(np.float32)


def get_centroid_manifest(datasource_name):
    global config
    if config is None or datasource_name not in config:
        load_config(datasource_name)
    return centroid_tiles.get_manifest(config, datasource_name, build=True)


def get_centroid_tiles(datasource_name, level, tiles, gates=None, max_points=None):
    global config
    if config is None or datasource_name not in config:
        load_config(datasource_name)
    return centroid_tiles.get_tiles(config, datasource_name, level, tiles, gates or {}, max_points)


def download_gating_csv(datasource_name, gates, channels, selection_ids, encoding):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    _ensure_loaded(datasource_name)

    # Polars' with_columns always returns a new frame, so (unlike pandas'
    # in-place .loc mutation) there's no risk to the shared global from
    # building the per-channel columns below without a defensive .copy().
    csv = datasource

    columns = []
    if 'idField' in config[datasource_name]['featureData'][0]:
        idField = config[datasource_name]['featureData'][0]['idField']
    else:
        idField = "CellID"
    columns.append(idField)

    if selection_ids:
        datasource_filter = datasource.filter(pl.col(idField).is_in(selection_ids))
    else:
        datasource_filter = datasource

    expr = None
    for key, value in gates.items():
        columns.append(key)
        cond = (pl.col(key) > value[0]) & (pl.col(key) < value[1])
        expr = cond if expr is None else (expr & cond)
    if expr is not None:
        ids = datasource_filter.filter(expr)['id'].to_numpy()
    else:
        # No gates set: no filter, nothing gated in. (pandas' .query('')
        # used to raise ValueError here -- fixed rather than preserved.)
        ids = np.array([], dtype=np.int64)

    if 'Area' in channels:
        del channels['Area']
    is_in_ids = pl.col('id').is_in(ids)
    for channel in channels:
        if channel in gates:
            # Cast to the original column's dtype for CSV-text parity with
            # the pandas version: csv.loc[mask, channel] = 1 silently
            # upcast an int literal into what's typically a float64 marker
            # column (rendering "1.0"), whereas a bare Polars int literal
            # would render "1" -- a real text diff in the exported CSV.
            dtype = csv.schema[channel]
            if encoding == 'binary':
                value_expr = pl.when(is_in_ids).then(pl.lit(1)).otherwise(pl.lit(0)).cast(dtype)
            else:
                value_expr = pl.when(is_in_ids).then(pl.col(channel)).otherwise(pl.lit(0).cast(dtype))
            csv = csv.with_columns(value_expr.alias(channel))
        else:
            csv = csv.with_columns(pl.lit(0).alias(channel))

    return csv


def download_gates(datasource_name, gates, channels, lassos):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    _ensure_loaded(datasource_name)
    rows = []
    for key, value in channels.items():
        rows.append([key, value[0], value[1]])
    csv = pl.DataFrame(rows, schema=['channel', 'gate_start', 'gate_end'], orient='row')
    csv = csv.with_columns(pl.lit(False).alias('gate_active'))

    schema = csv.schema
    for channel in gates:
        is_channel = pl.col('channel') == channel
        csv = csv.with_columns([
            pl.when(is_channel).then(pl.lit(True)).otherwise(pl.col('gate_active')).alias('gate_active'),
            pl.when(is_channel).then(pl.lit(gates[channel][0]).cast(schema['gate_start']))
              .otherwise(pl.col('gate_start')).alias('gate_start'),
            pl.when(is_channel).then(pl.lit(gates[channel][1]).cast(schema['gate_end']))
              .otherwise(pl.col('gate_end')).alias('gate_end'),
        ])

    if len(lassos) > 0:
        # Confirmed dead in current live usage (imageViewer.js permanently
        # sets list_lassos = {} since lasso drawing was removed), but
        # implemented correctly rather than skipped. lasso_polygon is a
        # nested structure that won't unify with the float gate columns
        # above, so build it as its own frame and concat with relaxed
        # schema-widening instead of forcing one shared schema up front.
        lasso_rows = [
            {'channel': 'Lasso', 'gate_start': v['lasso_polygon'], 'gate_end': None, 'gate_active': v['lasso_toggle']}
            for v in lassos.values()
        ]
        lasso_df = pl.DataFrame(lasso_rows, strict=False)
        csv = pl.concat([csv, lasso_df], how='diagonal_relaxed')

    return csv


def save_gating_list(datasource_name, gates, channels, lassos):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    _ensure_loaded(datasource_name)
    rows = []
    for key, value in channels.items():
        rows.append([key, value[0], value[1]])
    csv = pl.DataFrame(rows, schema=['channel', 'gate_start', 'gate_end'], orient='row')
    csv = csv.with_columns(pl.lit(False).alias('gate_active'))

    schema = csv.schema
    for channel in gates:
        is_channel = pl.col('channel') == channel
        csv = csv.with_columns([
            pl.when(is_channel).then(pl.lit(True)).otherwise(pl.col('gate_active')).alias('gate_active'),
            pl.when(is_channel).then(pl.lit(gates[channel][0]).cast(schema['gate_start']))
              .otherwise(pl.col('gate_start')).alias('gate_start'),
            pl.when(is_channel).then(pl.lit(gates[channel][1]).cast(schema['gate_end']))
              .otherwise(pl.col('gate_end')).alias('gate_end'),
        ])

    if len(lassos) > 0:
        lasso_rows = [
            {'channel': 'Lasso', 'gate_start': v['lasso_polygon'], 'gate_end': None, 'gate_active': v['lasso_toggle']}
            for v in lassos.values()
        ]
        lasso_df = pl.DataFrame(lasso_rows, strict=False)
        csv = pl.concat([csv, lasso_df], how='diagonal_relaxed')

    temp = csv.to_dicts()
    f = pickle.dumps(temp, protocol=4)
    database_model.save_list(database_model.GatingList, datasource=datasource_name, cells=f)


def get_saved_gating_list(datasource_name):
    gating_list = database_model.get(database_model.GatingList, datasource=datasource_name)
    if gating_list is None:
        return None
    return pickle.loads(gating_list.cells)


def download_channels(datasource_name, map_channels, active_channels, list_colors, list_ranges, list_channels):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    _ensure_loaded(datasource_name)
    rows = []
    for channel in map_channels:
        channel_name = map_channels[channel]
        rows.append([channel_name, list_channels[channel_name][0], list_channels[channel_name][1], 255, 255, 255, 1, False])
    csv = pl.DataFrame(rows, schema=['channel', 'start', 'end', 'r', 'g', 'b', 'opacity', 'channel_active'], orient='row')

    schema = csv.schema
    for channel in list_colors:
        is_channel = pl.col('channel') == map_channels[channel]
        color = list_colors[channel]['color']
        csv = csv.with_columns([
            pl.when(is_channel).then(pl.lit(color['r']).cast(schema['r'])).otherwise(pl.col('r')).alias('r'),
            pl.when(is_channel).then(pl.lit(color['g']).cast(schema['g'])).otherwise(pl.col('g')).alias('g'),
            pl.when(is_channel).then(pl.lit(color['b']).cast(schema['b'])).otherwise(pl.col('b')).alias('b'),
            pl.when(is_channel).then(pl.lit(color['opacity']).cast(schema['opacity'])).otherwise(pl.col('opacity')).alias('opacity'),
        ])
    for channel in active_channels:
        is_channel = pl.col('channel') == map_channels[channel]
        csv = csv.with_columns(
            pl.when(is_channel).then(pl.lit(True)).otherwise(pl.col('channel_active')).alias('channel_active')
        )

    return csv


def save_channel_list(datasource_name, map_channels, active_channels, list_colors, list_ranges, list_channels):
    global datasource
    global source
    global ball_tree

    # Load if not loaded
    _ensure_loaded(datasource_name)
    rows = []
    for channel in map_channels:
        channel_name = map_channels[channel]
        rows.append([channel_name, list_channels[channel_name][0], list_channels[channel_name][1], 255, 255, 255, 1, False])
    csv = pl.DataFrame(rows, schema=['channel', 'start', 'end', 'r', 'g', 'b', 'opacity', 'channel_active'], orient='row')

    schema = csv.schema
    for channel in list_colors:
        is_channel = pl.col('channel') == map_channels[channel]
        color = list_colors[channel]['color']
        csv = csv.with_columns([
            pl.when(is_channel).then(pl.lit(color['r']).cast(schema['r'])).otherwise(pl.col('r')).alias('r'),
            pl.when(is_channel).then(pl.lit(color['g']).cast(schema['g'])).otherwise(pl.col('g')).alias('g'),
            pl.when(is_channel).then(pl.lit(color['b']).cast(schema['b'])).otherwise(pl.col('b')).alias('b'),
            pl.when(is_channel).then(pl.lit(color['opacity']).cast(schema['opacity'])).otherwise(pl.col('opacity')).alias('opacity'),
        ])
    for channel in active_channels:
        is_channel = pl.col('channel') == map_channels[channel]
        csv = csv.with_columns(
            pl.when(is_channel).then(pl.lit(True)).otherwise(pl.col('channel_active')).alias('channel_active')
        )

    temp = csv.to_dicts()
    f = pickle.dumps(temp, protocol=4)
    database_model.save_list(database_model.ChannelList, datasource=datasource_name, cells=f)




def get_saved_channel_list(datasource_name):
    channel_list = database_model.get(database_model.ChannelList, datasource=datasource_name)
    if channel_list is None:
        return None
    return pickle.loads(channel_list.cells)


def _describe_numeric(df):
    """Vectorized equivalent of df.describe().to_dict() for numeric columns.
    Avoids pandas' per-column describe() loop, which is slow at millions of
    rows across dozens of columns.
    """
    numeric_df = df.select(cs.numeric())
    values = numeric_df.cast(pl.Float64).to_numpy()
    count = np.sum(~np.isnan(values), axis=0)
    mean = np.nanmean(values, axis=0)
    std = np.nanstd(values, axis=0, ddof=1)
    minimum = np.nanmin(values, axis=0)
    maximum = np.nanmax(values, axis=0)
    q25, q50, q75 = np.nanpercentile(values, [25, 50, 75], axis=0)
    description = {}
    for i, column in enumerate(numeric_df.columns):
        description[column] = {
            'count': count[i],
            'mean': mean[i],
            'std': std[i],
            'min': minimum[i],
            '25%': q25[i],
            '50%': q50[i],
            '75%': q75[i],
            'max': maximum[i],
        }
    return description



def get_datasource_description(datasource_name):
    global datasource
    global source
    global ball_tree
    global config

    # Load if not loaded
    _ensure_loaded(datasource_name)

    if datasource_name in _description_cache:
        return _description_cache[datasource_name]

    description = _describe_numeric(datasource)
    for column in description:
        column_data = datasource[column].to_numpy()
        [hist, bin_edges] = np.histogram(column_data[~np.isnan(column_data)], bins=50, density=True)
        midpoints = (bin_edges[1:] + bin_edges[:-1]) / 2
        description[column]['histogram'] = {}
        dat = []
        for i in range(len(hist)):
            obj = {}
            obj['x'] = midpoints[i]
            obj['y'] = hist[i]
            dat.append(obj)
        description[column]['histogram'] = dat

    list_channels = config[datasource_name]['imageData']
    image_layer = 0
    for channel in list_channels:
        if channel['name'] != 'Area':
            fullName = channel['fullname']

            image_data = zarray[image_layer]
            img_log = np.log(image_data[image_data > 0])
            [hist, bin_edges] = np.histogram(img_log.flatten(), bins=50, density=True)
            midpoints = (bin_edges[1:] + bin_edges[:-1]) / 2
            description[fullName]['image_histogram'] = {}

            dat = []
            for i in range(len(hist)):
                obj = {}
                obj['x'] = midpoints[i]
                obj['y'] = hist[i]
                dat.append(obj)

            description[fullName]['image_histogram'] = dat
            description[fullName]['image_min'] = np.ceil(np.exp(np.min(img_log)))
            description[fullName]['image_max'] = np.ceil(np.exp(np.max(img_log)))

            image_layer += 1
        else:
            continue

    _description_cache[datasource_name] = description
    return description


def get_channel_gmm(channel_name, datasource_name):
    global datasource
    global source
    global ball_tree
    global config

    # Load if not loaded
    _ensure_loaded(datasource_name)

    cache_key = (datasource_name, channel_name)
    if cache_key in _gmm_cache:
        return _gmm_cache[cache_key]

    packet_gmm = {}

    image_channelIdx = next(
        index for (index, d) in enumerate(config[datasource_name]['imageData']) if d["fullname"] == channel_name) - 1
    image_data = zarray[image_channelIdx]
    img_log = np.log(image_data[image_data > 0])
    gmm = GaussianMixture(3, max_iter=1000, tol=1e-6)
    gmm.fit(img_log.reshape((-1, 1)))

    means = gmm.means_[:, 0]
    i0, i1, i2 = np.argsort(means)
    mean1, mean2 = means[[i1, i2]]
    std1, std2 = gmm.covariances_[[i1, i2], 0, 0] ** 0.5

    x = np.linspace(mean1, mean2, 50)
    y1 = norm(mean1, std1).pdf(x) * gmm.weights_[i1]
    y2 = norm(mean2, std2).pdf(x) * gmm.weights_[i2]

    lmax = mean2 + 2 * std2
    lmin = x[np.argmin(np.abs(y1 - y2))]
    if lmin >= mean2:
        lmin = mean2 - 2 * std2
    vmin = max(np.exp(lmin), image_data.min(), 0)
    vmax = min(np.exp(lmax), image_data.max())

    packet_gmm['vmin'] = np.rint(vmin)
    packet_gmm['vmax'] = np.rint(vmax)

    [hist, bin_edges] = np.histogram(img_log.flatten(), bins=50, density=True)
    midpoints = (bin_edges[1:] + bin_edges[:-1]) / 2

    covars = gmm.covariances_[:, 0, 0]
    weights = gmm.weights_
    pdf_gmm1 = weights[i0] * norm.pdf(midpoints, means[i0], np.sqrt(covars[i0]))
    pdf_gmm2 = weights[i1] * norm.pdf(midpoints, means[i1], np.sqrt(covars[i1]))
    pdf_gmm3 = weights[i2] * norm.pdf(midpoints, means[i2], np.sqrt(covars[i2]))

    dat_gmm1 = []
    dat_gmm2 = []
    dat_gmm3 = []
    for i in range(len(hist)):
        obj1 = {}
        obj1['x'] = midpoints[i]
        obj1['y'] = pdf_gmm1[i]
        dat_gmm1.append(obj1)

        obj2 = {}
        obj2['x'] = midpoints[i]
        obj2['y'] = pdf_gmm2[i]
        dat_gmm2.append(obj2)

        obj3 = {}
        obj3['x'] = midpoints[i]
        obj3['y'] = pdf_gmm3[i]
        dat_gmm3.append(obj3)

    packet_gmm['image_gmm_1'] = dat_gmm1
    packet_gmm['image_gmm_2'] = dat_gmm2
    packet_gmm['image_gmm_3'] = dat_gmm3

    _gmm_cache[cache_key] = packet_gmm
    return packet_gmm


def get_gating_gmm(channel_name, datasource_name, selection_ids):
    global datasource
    global source
    global ball_tree
    global config

    # Load if not loaded
    _ensure_loaded(datasource_name)

    selection_key = tuple(sorted(selection_ids)) if selection_ids else None
    cache_key = (datasource_name, channel_name, selection_key)
    if cache_key in _gmm_cache:
        return _gmm_cache[cache_key]

    packet_gmm = {}

    if 'idField' in config[datasource_name]['featureData'][0]:
        idField = config[datasource_name]['featureData'][0]['idField']
    else:
        idField = "CellID"
    if selection_ids:
        datasource_filter = datasource.filter(pl.col(idField).is_in(selection_ids))
    else:
        # No selection to filter by (the only case current callers use,
        # since lasso/spatial-selection was removed) -- avoid a full
        # 2M-row copy that's immediately discarded.
        datasource_filter = datasource

    column_data = datasource[channel_name].to_numpy()
    [hist, bin_edges] = np.histogram(column_data[~np.isnan(column_data)], bins=50, density=True)
    midpoints = (bin_edges[1:] + bin_edges[:-1]) / 2

    column_data_filtered = datasource_filter[channel_name].to_numpy()

    # Cap the GMM fit input at a random subsample when the cell-level column
    # is large -- EM cost scales roughly linearly with N per iteration, and
    # a 2-component 1D mixture's fitted parameters barely move between 100k
    # and millions of samples. Fixed seed keeps the fit deterministic per
    # unique _gmm_cache key. The histogram above is intentionally left
    # unaffected -- only the .fit() input is capped. get_channel_gmm (image
    # pixel data, already ~40k points after block_reduce) is not capped.
    GMM_FIT_SAMPLE_CAP = 100_000
    fit_data = column_data_filtered
    if fit_data.shape[0] > GMM_FIT_SAMPLE_CAP:
        rng = np.random.default_rng(0)
        fit_data = fit_data[rng.choice(fit_data.shape[0], size=GMM_FIT_SAMPLE_CAP, replace=False)]

    gmm = GaussianMixture(n_components=2)
    gmm.fit(fit_data.reshape((-1, 1)))
    i0, i1 = np.argsort(gmm.means_[:, 0])
    packet_gmm['gate'] = np.mean(gmm.means_)

    pdf_gmm1 = [gmm.weights_[i0] * norm.pdf(midpoints, gmm.means_[i0], np.sqrt(gmm.covariances_[i0]))][0][0]
    pdf_gmm2 = [gmm.weights_[i1] * norm.pdf(midpoints, gmm.means_[i1], np.sqrt(gmm.covariances_[i1]))][0][0]

    dat_gmm1 = []
    dat_gmm2 = []
    for i in range(len(hist)):
        obj1 = {}
        obj1['x'] = midpoints[i]
        obj1['y'] = pdf_gmm1[i]
        dat_gmm1.append(obj1)

        obj2 = {}
        obj2['x'] = midpoints[i]
        obj2['y'] = pdf_gmm2[i]
        dat_gmm2.append(obj2)

    packet_gmm['gmm_1'] = dat_gmm1
    packet_gmm['gmm_2'] = dat_gmm2

    _gmm_cache[cache_key] = packet_gmm
    return packet_gmm


def generate_zarr_png(datasource_name, channel, level, tile):
    global channels
    global seg
    if source != datasource_name or config is None or channels is None or seg is None:
        load_datasource(datasource_name)
    [tx, ty] = tile.replace('.png', '').split('_')
    tx = int(tx)
    ty = int(ty)
    level = int(level)
    tile_width = config[datasource_name]['tileWidth']
    tile_height = config[datasource_name]['tileHeight']
    ix = tx * tile_width
    iy = ty * tile_height
    segmentation = False
    try:
        channel_num = int(re.match(r".*_(\d*)$", channel).groups()[0])
    except AttributeError:
        segmentation = True
    if segmentation:
        tile = _zarr_level(seg, level)[iy:iy + tile_height, ix:ix + tile_width]
        if tile.dtype.itemsize != 4:
            tile = tile.astype(np.uint32)
        tile = tile.view('uint8').reshape(tile.shape + (-1,))[..., [0, 1, 2]]
        tile = np.append(tile, np.zeros((tile.shape[0], tile.shape[1], 1), dtype='uint8'), axis=2)
    else:
        if isinstance(channels, zarr.Array):
            tile = channels[channel_num, iy:iy + tile_height, ix:ix + tile_width]
        else:
            tile = _zarr_level(channels, level)[channel_num, iy:iy + tile_height, ix:ix + tile_width]
            tile = tile.astype('uint16')

    # tile = np.ascontiguousarray(tile, dtype='uint32')
    # png = tile.view('uint8').reshape(tile.shape + (-1,))[..., [2, 1, 0]]
    return tile


def get_ome_metadata(datasource_name):
    global metadata
    if source != datasource_name or config is None or metadata is None:
        load_datasource(datasource_name)
    return metadata


def convertOmeTiff(filePath, channelFilePath=None, dataDirectory=None, isLabelImg=False):
    channel_info = {}
    channelNames = []

    # image is a normal channel?
    if isLabelImg == False:
        channel_io = tf.TiffFile(str(filePath), is_ome=False)
        channels = zarr.open(channel_io.series[0].aszarr())
        if isinstance(channels, zarr.Array):
            channel_info['maxLevel'] = 1
            chunks = channels.chunks
            shape = channels.shape
        else:
            channel_info['maxLevel'] = len(channels)
            shape = _zarr_level(channels, 0).shape
            chunks = (1, 1024, 1024)
        chunks = (chunks[-2], chunks[-1])
        channel_info['tileHeight'] = chunks[0]
        channel_info['tileWidth'] = chunks[1]
        channel_info['height'] = shape[1]
        channel_info['width'] = shape[2]
        channel_info['num_channels'] = shape[0]
        for i in range(shape[0]):
            channelName = re.sub(r'\.ome\.tiff|\.ome\.tif|\.tiff|\.tif|\.png', '', filePath.name) + "_" + str(i)
            channelNames.append(channelName)
        channel_info['channel_names'] = channelNames
        return channel_info

    # segmentation mask
    else:
        channel_io = tf.TiffFile(str(channelFilePath), is_ome=False)
        channels = zarr.open(channel_io.series[0].aszarr())
        write_path = None
        directory = Path(dataDirectory + "/" + filePath.name)
        segmentation_mask = tf.TiffFile(str(filePath), is_ome=False)
        if segmentation_mask.series[0].aszarr().is_multiscales is False:
            args = {}
            args['in_paths'] = [Path(filePath)]
            args['out_path'] = directory
            args['is_mask'] = True
            pyramid_assemble.main(py_args=args)
            pyramid_upgrade.main(py_args=args)
            write_path = str(directory)
        else:
            write_path = str(filePath)
        write_path = ensure_outline_segmentation(write_path, dataDirectory)
        return {'segmentation': write_path}


def logTransform(csvPath, skip_columns=[]):
    df = pl.read_csv(csvPath)
    transform_cols = [c for c in df.columns if c not in skip_columns]
    df = df.with_columns([pl.col(c).log1p().alias(c) for c in transform_cols])
    df.write_csv(csvPath)

