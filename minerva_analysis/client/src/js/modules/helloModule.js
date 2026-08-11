/**
 * helloModule.js - throwaway module proving the Phase 1-3 extension seam actually works
 * end to end (Phase 4 of the modularization plan): a second module registering itself,
 * adding a server route, and adding a sidebar panel, without editing data_model.py,
 * main.js, imageViewer.js, or any core template markup -- base.html only gains one
 * conditional <script> include (mirroring csvGatingList.js's), the same allowance the
 * plan calls out. The panel itself is built and appended entirely in JS (into the core
 * #viewer_sidebar container), rather than relying on template markup, so a real add-on
 * module never needs an index.html edit either.
 *
 * Not wired into any shipped build (MINERVA_ACTIVE_MODULE defaults to "gating"). Safe to
 * delete once a real second module (e.g. roi) exists -- copy this file's shape instead of
 * building on top of it, per the plan's own instructions to whoever builds that module.
 */
class HelloSidebarController {
    setup() {
        const sidebar = document.getElementById("viewer_sidebar");
        if (!sidebar) return;

        const section = document.createElement("section");
        section.className = "sidebar-section";
        section.id = "hello_module_section";
        section.innerHTML = `
            <h3>Hello Module</h3>
            <button id="hello_ping_button" class="sidebar-action" type="button">Ping server</button>
            <div id="hello_ping_result"></div>
        `;
        sidebar.appendChild(section);

        section.querySelector("#hello_ping_button").addEventListener("click", async () => {
            const result = section.querySelector("#hello_ping_result");
            result.textContent = "...";
            try {
                const response = await fetch(minervaUrl("hello_ping"));
                const data = await response.json();
                result.textContent = data.message;
            } catch (error) {
                result.textContent = `Error: ${error.message}`;
            }
        });
    }
}

if (window.AppModules) {
    window.AppModules.register({
        name: "hello",
        createSidebarController() {
            return new HelloSidebarController();
        },
    });
}
