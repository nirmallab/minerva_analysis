from pathlib import Path

from flask import jsonify, render_template, request

from minerva_analysis import app, get_config
from minerva_analysis.datasource import derive_anndata_channel_names, register_anndata_datasource
from minerva_analysis.server.models import data_model
from minerva_analysis.server.models.adapters.inspection import inspect_anndata
from minerva_analysis.server.routes.import_routes import trim_filepath_quotes
from minerva_analysis.server.routes.page_routes import template_data


@app.route('/import_anndata', methods=['POST'])
def import_anndata():
    """Step 1 of the AnnData import flow: collect image/segmentation/.h5ad
    paths, inspect the .h5ad structure, and render the progressive-disclosure
    config page. Mirrors import_routes.py's upload_file_page()'s pattern of
    rendering the next page directly from a POST rather than a JSON redirect.
    """
    name = (request.form.get('name') or '').strip()
    # Paths pasted with surrounding quotes (e.g. copied from a shell or a
    # message) validate fine in the live as-you-type check (check_file_existence
    # strips them via trim_filepath_quotes) -- strip them here too, or a
    # quoted path that showed green while typing fails this re-check on submit.
    image_path = trim_filepath_quotes((request.form.get('channel_file') or '').strip())
    segmentation_path = trim_filepath_quotes((request.form.get('label_file') or '').strip())
    features_path = trim_filepath_quotes((request.form.get('h5ad_file') or '').strip())

    errors = []
    if not name:
        errors.append("Dataset name is required.")
    elif name in get_config():
        errors.append(f"A dataset named '{name}' already exists.")
    if not image_path or not Path(image_path).exists():
        errors.append("Image file path does not exist.")
    if not features_path or not Path(features_path).exists():
        errors.append("AnnData (.h5ad) file path does not exist.")
    if segmentation_path and not Path(segmentation_path).exists():
        errors.append("Segmentation file path does not exist.")

    if errors:
        return render_template('upload.html', data=template_data(error=" ".join(errors)))

    try:
        inspection = inspect_anndata(features_path)
    except Exception as exc:
        return render_template('upload.html', data=template_data(error=f"Could not read AnnData file: {exc}"))

    # Reading channel count from the image is cheap for the main channel
    # image (metadata-only zarr shape read, no pyramid write -- that only
    # happens for the segmentation mask) so it's safe to do here, before the
    # user commits, to preview which name source will actually apply.
    try:
        n_channels = data_model.convertOmeTiff(Path(image_path), isLabelImg=False)['num_channels']
    except Exception as exc:
        return render_template('upload.html', data=template_data(error=f"Could not read image file: {exc}"))

    channel_names, channel_names_source = derive_anndata_channel_names(
        Path(image_path), features_path, n_channels
    )

    inspection.update({
        'name': name,
        'image': image_path,
        'segmentation': segmentation_path or None,
        'features': features_path,
        'channel_names': channel_names,
        'channel_names_source': channel_names_source,
    })
    return render_template('datasource_config.html', data=template_data(**inspection))


@app.route('/save_datasource_config', methods=['POST'])
def save_datasource_config():
    """Step 2: the resolved config the user picked on the AnnData config page.
    register_anndata_datasource() itself validates end-to-end (subset/
    coordinates/features resolve, IDs unique, etc.) before writing config.json
    or doing any image-pyramid work, so a ValueError here means nothing was
    written -- safe to just report the message back to the page.
    """
    payload = request.get_json(silent=True) or {}
    name = payload.get('name')
    try:
        if not name:
            raise ValueError("Dataset name is required.")
        register_anndata_datasource(
            name=name,
            image=payload.get('image'),
            features=payload.get('features'),
            segmentation=payload.get('segmentation') or None,
            coordinate_source=payload.get('coordinate_source') or None,
            obsm_key=payload.get('obsm_key') or None,
            x=payload.get('x') or None,
            y=payload.get('y') or None,
            feature_source=payload.get('feature_source') or 'X',
            layer=payload.get('layer') or None,
            feature_obs_columns=payload.get('feature_obs_columns') or None,
            obs_id_field=payload.get('obs_id_field') or None,
            celltype_column=payload.get('celltype_column') or None,
            subset_by=payload.get('subset_by') or None,
            subset_value=payload.get('subset_value'),
            apply_log_transform=bool(payload.get('apply_log_transform')),
            channel_names=payload.get('channel_names') or None,
        )
    except Exception as exc:
        return jsonify(success=False, error=str(exc)), 400

    return jsonify(success=True, name=name)
