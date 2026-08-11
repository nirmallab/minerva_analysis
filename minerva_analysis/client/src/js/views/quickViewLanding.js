/**
 * quickViewLanding.js -- wires the home-page (index.html's {% else %}
 * branch, shown when no datasource is loaded yet). The full path to a
 * local image is always POSTed to /quick_view -- never the file's bytes,
 * so huge OME-TIFFs load instantly instead of being copied over HTTP.
 *
 * Browsers do not expose a File's absolute filesystem path to a regular
 * web page (Chrome only ever did this for Electron/packaged-app contexts,
 * never for an ordinary page like this one) -- so the path input is the
 * primary, always-visible way to load an image. Drag-and-drop / click-to-
 * browse is a convenience on top of it: it fills in the filename (the one
 * thing a File object *does* expose) so the user only has to complete the
 * folder part, and opportunistically submits immediately if a nonstandard
 * File.path ever is present (e.g. some embedding contexts).
 */
(function () {
    const dropzone = document.getElementById("quick_view_dropzone");
    const fileInput = document.getElementById("quick_view_file_input");
    const pathInput = document.getElementById("quick_view_path_input");
    const loadButton = document.getElementById("quick_view_path_load");
    const status = document.getElementById("quick_view_status");

    if (!dropzone) {
        return;
    }

    function setStatus(message, isError) {
        if (!message) {
            status.hidden = true;
            status.textContent = "";
            status.classList.remove("error");
            return;
        }
        status.hidden = false;
        status.textContent = message;
        status.classList.toggle("error", !!isError);
    }

    function setBusy(busy) {
        dropzone.style.pointerEvents = busy ? "none" : "";
        loadButton.disabled = busy || !pathInput.value.trim();
    }

    async function submitQuickView(path) {
        setBusy(true);
        setStatus("Loading " + path.split(/[\\/]/).pop() + "...", false);
        try {
            const response = await fetch(minervaUrl("quick_view"), {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({path: path}),
            });
            const result = await response.json();
            if (!response.ok || !result.success) {
                throw new Error(result.error || "Could not load that image.");
            }
            window.location.href = result.redirect;
        } catch (error) {
            setBusy(false);
            setStatus(error.message || "Could not load that image.", true);
        }
    }

    function handleFile(file) {
        if (!file) {
            return;
        }
        if (file.path) {
            submitQuickView(file.path);
            return;
        }
        // No absolute path available -- pre-fill just the filename and put
        // the cursor at the start so the user can type/paste the folder
        // part in front of it.
        pathInput.value = file.name || "";
        pathInput.focus();
        pathInput.setSelectionRange(0, 0);
        pathInput.dispatchEvent(new Event("input"));
    }

    dropzone.addEventListener("click", () => fileInput.click());
    dropzone.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            fileInput.click();
        }
    });
    fileInput.addEventListener("change", () => handleFile(fileInput.files[0]));

    ["dragenter", "dragover"].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
            event.preventDefault();
            dropzone.classList.add("drag-over");
        });
    });
    ["dragleave", "dragend"].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
            event.preventDefault();
            dropzone.classList.remove("drag-over");
        });
    });
    dropzone.addEventListener("drop", (event) => {
        event.preventDefault();
        dropzone.classList.remove("drag-over");
        handleFile(event.dataTransfer.files[0]);
    });

    let validationRequestId = 0;
    pathInput.addEventListener("input", async () => {
        const path = pathInput.value.trim();
        pathInput.classList.remove("is-invalid");
        loadButton.disabled = true;
        if (!path) {
            return;
        }
        const requestId = ++validationRequestId;
        try {
            const response = await fetch(minervaUrl("check_file_existence"), {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({path: path}),
            });
            const exists = await response.json();
            if (requestId !== validationRequestId) {
                return;
            }
            pathInput.classList.toggle("is-invalid", !exists);
            loadButton.disabled = !exists;
        } catch (error) {
            if (requestId === validationRequestId) {
                pathInput.classList.add("is-invalid");
            }
        }
    });

    pathInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !loadButton.disabled) {
            submitQuickView(pathInput.value.trim());
        }
    });

    loadButton.addEventListener("click", () => {
        const path = pathInput.value.trim();
        if (path) {
            submitQuickView(path);
        }
    });
})();
