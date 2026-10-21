/* 应用入口。MC.config 由 index.html 注入。 */
(function (global) {
  "use strict";
  var MC = global.MC;
  function boot() {
    var app = new MC.App(document.getElementById("app"));
    MC.appInstance = app;
    app.start().catch(function (e) { console.error("[boot]", e); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(window);
