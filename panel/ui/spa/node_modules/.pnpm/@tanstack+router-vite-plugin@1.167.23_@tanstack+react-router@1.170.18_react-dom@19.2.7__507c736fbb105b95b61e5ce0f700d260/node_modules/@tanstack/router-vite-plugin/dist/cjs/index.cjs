var _tanstack_router_plugin_vite = require("@tanstack/router-plugin/vite");
Object.keys(_tanstack_router_plugin_vite).forEach(function(k) {
	if (k !== "default" && !Object.prototype.hasOwnProperty.call(exports, k)) Object.defineProperty(exports, k, {
		enumerable: true,
		get: function() {
			return _tanstack_router_plugin_vite[k];
		}
	});
});
