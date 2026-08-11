import argparse
import os


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Minerva Analysis as a notebook-friendly sidecar server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="8000")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--notebook-mode", action="store_true")
    parser.add_argument("--active-module", default=None)
    args = parser.parse_args(argv)

    if args.data_dir:
        os.environ["MINERVA_DATA_PATH"] = args.data_dir
    if args.base_url is not None:
        os.environ["MINERVA_BASE_URL"] = args.base_url
    if args.notebook_mode:
        os.environ["MINERVA_NOTEBOOK_MODE"] = "1"
    if args.active_module is not None:
        os.environ["MINERVA_ACTIVE_MODULE"] = args.active_module

    from waitress import serve
    from minerva_analysis import app, _clean_base_url

    app.config["MINERVA_NOTEBOOK_MODE"] = args.notebook_mode or app.config.get("MINERVA_NOTEBOOK_MODE", False)
    if args.base_url is not None:
        app.config["MINERVA_BASE_URL"] = _clean_base_url(args.base_url)
    # Module registration (Blueprint mounting) already happened inside
    # create_app() at the `from minerva_analysis import app` line above,
    # keyed off the MINERVA_ACTIVE_MODULE env var set above -- unlike
    # MINERVA_BASE_URL/MINERVA_NOTEBOOK_MODE, there's no post-import
    # app.config override that could retroactively register a Blueprint.
    print(f"Serving Minerva Analysis on {args.host}:{args.port}")
    serve(
        app,
        host=args.host,
        port=int(args.port),
        max_request_body_size=1073741824000000,
        max_request_header_size=85899345920000,
        threads=8,
    )


if __name__ == "__main__":
    main()
