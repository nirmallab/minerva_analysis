"""Registry of optional feature modules (gating today; roi or others in
future). Each module is a package under minerva_analysis/server/modules/
exposing a register(app) function that attaches its own Flask Blueprint(s).

Exactly one module is active per running process, chosen via the
MINERVA_ACTIVE_MODULE env var (see minerva_analysis/__init__.py's
create_app(), and jupyter.py/server_cli.py/proxy.py/run.py for how that
env var gets set at launch time).

Loaders are lazy (imported only when actually selected) so a build that
never activates a given module never pays its import cost -- e.g. a
gating-free process never imports h5py/anndata via the gating module's
anndata_gates submodule.
"""


def _load_gating():
    from minerva_analysis.server.modules.gating import register
    return register


def _load_hello():
    from minerva_analysis.server.modules.hello import register
    return register


MODULES = {
    "gating": _load_gating,
    # Phase-4 seam-completeness check (see the modularization plan) -- a
    # throwaway module, not a real feature. Remove once a real second module
    # (e.g. roi) lands and this has served its purpose.
    "hello": _load_hello,
}


def register_active_module(app, name):
    """No-ops for an unknown/empty module name, so a core build with no
    modules installed (or an unrecognized MINERVA_ACTIVE_MODULE value)
    still starts cleanly with just the core routes."""
    loader = MODULES.get(name)
    if loader is None:
        return
    register = loader()
    register(app)
