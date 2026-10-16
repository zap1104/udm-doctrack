"""The standalone checker understands names in included route modules."""
from scripts import check_templates


def test_included_routes_inherit_namespace_without_accepting_unregistered_modules(tmp_path, monkeypatch):
    app = tmp_path / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "urls.py").write_text(
        'app_name = "demo"\nurlpatterns = [path("external/", include("apps.demo.external_urls"))]\n',
        encoding="utf-8",
    )
    (app / "external_urls.py").write_text('urlpatterns = [path("", view, name="external_list")]\n', encoding="utf-8")
    (app / "private_urls.py").write_text('urlpatterns = [path("", view, name="unregistered")]\n', encoding="utf-8")
    monkeypatch.setattr(check_templates, "ROOT", tmp_path)
    names = check_templates.known_url_names()
    assert "demo:external_list" in names
    assert "demo:unregistered" not in names
