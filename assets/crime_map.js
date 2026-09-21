/* Bridge native Plotly modifiers to the existing global Neighborhood control. */
window.dash_clientside = window.dash_clientside || {};
window.dash_clientside.crime_map = {
    bind_region_toggle: function () {
        function bind(id, attempts) {
            const container = document.getElementById(id);
            if (!container) return;
            const gd = container.querySelector(".js-plotly-plot");
            if (!gd || typeof gd.on !== "function") {
                if (attempts > 0) window.setTimeout(() => bind(id, attempts - 1), 50);
                return;
            }
            if (gd.__crimeRegionToggleBound) return;
            gd.__crimeRegionToggleBound = true;
            gd.on("plotly_click", function (eventData) {
                if (!eventData || !eventData.event ||
                    !(eventData.event.ctrlKey || eventData.event.metaKey)) return;
                const point = (eventData.points || [])[0];
                // Daily charts and point traces never change neighborhoods.
                if (!point || !point.data || point.data.type !== "choroplethmap" ||
                    !point.customdata || typeof point.customdata[5] !== "string") return;
                window.dash_clientside.set_props("crime-map-region-toggle", {
                    data: {
                        neighborhood: point.customdata[5],
                        timestamp: Date.now(),
                        sequence: (gd.__crimeRegionToggleSequence =
                            (gd.__crimeRegionToggleSequence || 0) + 1),
                        graph: id
                    }
                });
            });
        }
        window.setTimeout(function () {
            bind("crime-map-figure", 40);
            bind("crime-fullscreen-figure", 40);
        }, 0);
        return window.dash_clientside.no_update;
    }
};
