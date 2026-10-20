"""Single-page UI served at /. No build step, no framework, no CDN."""
PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A personal task tracker for daily planning</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif;
         max-width: 720px; margin: 2rem auto; padding: 0 1rem; }
  header h1 { font-size: 1.4rem; }
  section { margin: 2rem 0; }
  form input[type=text] { padding: .4rem .5rem; font: inherit; }
  ul { list-style: none; padding: 0; }
  li { display: flex; gap: .5rem; align-items: center; padding: .35rem 0;
       border-bottom: 1px solid rgba(127,127,127,.25); }
  li.done .t { text-decoration: line-through; opacity: .6; }
  .t { flex: 1; }
  button { cursor: pointer; }
  .error { color: #b3261e; min-height: 1.2rem; }
</style>
</head>
<body>
<header><h1>A personal task tracker for daily planning</h1><p id="err" class="error" role="alert"></p></header>
__SECTIONS__
<script>
function req(method, path, body) {
  return fetch(path, {
    method: method,
    headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined
  }).then(function (r) {
    return r.json().then(function (data) {
      if (!r.ok) { throw (data && data.error) ? data.error.message : r.status; }
      return data;
    });
  });
}
function showError(msg) { document.getElementById("err").textContent = msg || ""; }
function load(key) {
  return req("GET", "/api/" + key).then(function (items) {
    var ul = document.querySelector("[data-list='" + key + "']");
    ul.innerHTML = "";
    items.forEach(function (it) {
      var li = document.createElement("li");
      li.className = it.done ? "done" : "";
      var cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = !!it.done;
      cb.onchange = function () {
        req("PUT", "/api/" + key + "/" + it.id, {done: cb.checked})
          .then(function () { load(key); }).catch(showError);
      };
      var t = document.createElement("span");
      t.className = "t";
      t.textContent = it.title + (it.notes ? " — " + it.notes : "");
      var del = document.createElement("button");
      del.textContent = "delete";
      del.onclick = function () {
        req("DELETE", "/api/" + key + "/" + it.id)
          .then(function () { load(key); }).catch(showError);
      };
      li.appendChild(cb); li.appendChild(t); li.appendChild(del);
      ul.appendChild(li);
    });
  }).catch(showError);
}
document.querySelectorAll("[data-form]").forEach(function (form) {
  var key = form.getAttribute("data-form");
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    showError("");
    var title = form.querySelector("[name=title]").value;
    var notes = form.querySelector("[name=notes]").value;
    req("POST", "/api/" + key, {title: title, notes: notes})
      .then(function () { form.reset(); load(key); }).catch(showError);
  });
  load(key);
});
</script>
</body>
</html>
"""
