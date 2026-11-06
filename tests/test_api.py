"""API tests, parameterized over both backends via the injected repo."""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from models import ProductQuery

ALL_PRODUCT_FIELDS = {
    "id",
    "name",
    "description",
    "category",
    "brand",
    "price",
    "stock",
    "rating",
    "units_sold",
    "created_at",
}


def _all_products(repo):
    """Fetch every product from the repository (paginated)."""
    out = []
    page = 1
    while True:
        p = repo.list_products(ProductQuery(page=page, page_size=100))
        out.extend(p.items)
        if page >= p.total_pages or not p.items:
            break
        page += 1
    return out


def _is_iso_utc(value: str) -> bool:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return False
    return dt.utcoffset() is not None and dt.utcoffset().total_seconds() == 0


def test_list_products_shape(client, test_products):
    resp = client.get("/api/products")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == {"items", "total", "page", "page_size", "total_pages"}
    assert data["total"] == len(test_products)
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert len(data["items"]) == 20
    for item in data["items"]:
        assert set(item.keys()) == ALL_PRODUCT_FIELDS
        assert _is_iso_utc(item["created_at"])
        for entity_key in ("category", "brand"):
            if item[entity_key] is None:
                continue
            assert set(item[entity_key].keys()) == {"id", "name"}
            assert isinstance(item[entity_key]["id"], str) and item[entity_key]["id"]
            assert isinstance(item[entity_key]["name"], str)


def test_list_products_filter_and_sort(client):
    resp = client.get("/api/products?category=Books&sort_field=price&sort_ascending=true")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["items"], "expected at least one Books product"
    assert all(it["category"]["name"] == "Books" for it in data["items"])
    prices = [it["price"] for it in data["items"]]
    assert prices == sorted(prices)


def test_list_products_page2_disjoint_from_page1(client):
    p1 = client.get("/api/products?page=1&page_size=10").get_json()
    p2 = client.get("/api/products?page=2&page_size=10").get_json()
    ids1 = {it["id"] for it in p1["items"]}
    ids2 = {it["id"] for it in p2["items"]}
    assert not (ids1 & ids2)


def test_list_products_invalid_sort_field_400(client):
    assert client.get("/api/products?sort_field=bogus").status_code == 400


def test_list_products_unknown_query_params_ignored(client):
    resp = client.get("/api/products?category=Books&totally_bogus=123&another=xyz")
    assert resp.status_code == 200


def test_post_valid(client):
    payload = {
        "name": "New Product",
        "description": "d",
        "price": 12.5,
        "stock": 3,
        "rating": 4.0,
    }
    resp = client.post("/api/products", json=payload)
    assert resp.status_code == 201
    assert resp.headers.get("Location") == f"/api/products/{resp.get_json()['id']}"
    data = resp.get_json()
    assert data["id"]  # generated id present
    assert data["name"] == "New Product"
    assert data["price"] == 12.5
    # absent category / brand on the payload serialize as null
    assert data["category"] is None
    assert data["brand"] is None


def test_post_missing_name(client):
    resp = client.post("/api/products", json={"price": 12.5})
    assert resp.status_code == 400


def test_post_negative_price(client):
    resp = client.post("/api/products", json={"name": "X", "price": -5})
    assert resp.status_code == 400


def test_post_rating_out_of_range(client):
    resp = client.post("/api/products", json={"name": "X", "price": 12.5, "rating": 7.0})
    assert resp.status_code == 400


def test_post_malformed_json(client):
    resp = client.post("/api/products", data="{this is not json", content_type="application/json")
    assert resp.status_code == 400


def test_get_by_id(client):
    created = client.post("/api/products", json={"name": "FetchMe", "price": 9.9}).get_json()
    resp = client.get(f"/api/products/{created['id']}")
    assert resp.status_code == 200
    assert resp.get_json()["id"] == created["id"]


def test_get_unknown_id_404(client):
    assert client.get("/api/products/does-not-exist").status_code == 404


def test_get_malformed_id_404_not_500(client):
    assert client.get("/api/products/not-an-id").status_code == 404


def _put_payload(**overrides):
    payload = {
        "name": "Updated Product",
        "description": "updated description",
        "category": "Toys",
        "brand": "Globex",
        "price": 33.0,
        "stock": 9,
        "rating": 4.5,
        "units_sold": 77,
    }
    payload.update(overrides)
    return payload


def test_put_updates_product_and_preserves_identity(client):
    created = client.post("/api/products", json={"name": "ToEdit", "price": 10.0}).get_json()
    resp = client.put(f"/api/products/{created['id']}", json=_put_payload())
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == ALL_PRODUCT_FIELDS
    assert data["id"] == created["id"]
    assert data["created_at"] == created["created_at"]
    assert data["name"] == "Updated Product"
    assert data["description"] == "updated description"
    assert data["category"] is not None and data["category"]["name"] == "Toys"
    assert set(data["category"].keys()) == {"id", "name"}
    assert data["brand"] is not None and data["brand"]["name"] == "Globex"
    assert set(data["brand"].keys()) == {"id", "name"}
    assert data["price"] == 33.0
    assert data["stock"] == 9
    assert data["rating"] == 4.5
    assert data["units_sold"] == 77
    # the update is persisted
    got = client.get(f"/api/products/{created['id']}").get_json()
    assert got == data


def test_put_unknown_id_404(client):
    resp = client.put("/api/products/does-not-exist", json=_put_payload())
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


def test_put_malformed_id_404_not_500(client):
    resp = client.put("/api/products/not-an-id", json=_put_payload())
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


def test_put_negative_price_400(client):
    created = client.post("/api/products", json={"name": "ToEdit", "price": 10.0}).get_json()
    resp = client.put(f"/api/products/{created['id']}", json=_put_payload(price=-5))
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "price must be > 0"


def test_put_rating_out_of_range_400(client):
    created = client.post("/api/products", json={"name": "ToEdit", "price": 10.0}).get_json()
    resp = client.put(f"/api/products/{created['id']}", json=_put_payload(rating=7.0))
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "rating must be within [0, 5]"


def test_put_unknown_id_with_invalid_body_is_404_not_400(client):
    # the 404-over-400 priority order: existence is checked before validation
    resp = client.put("/api/products/does-not-exist", json={"name": "X", "price": -5})
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


def test_put_malformed_json_400(client):
    created = client.post("/api/products", json={"name": "ToEdit", "price": 10.0}).get_json()
    resp = client.put(
        f"/api/products/{created['id']}",
        data="{this is not json",
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "invalid JSON body"}


def test_delete_product(client):
    created = client.post("/api/products", json={"name": "DeleteMe", "price": 5.0}).get_json()
    pid = created["id"]
    assert client.delete(f"/api/products/{pid}").status_code == 204
    assert client.get(f"/api/products/{pid}").status_code == 404
    assert client.delete(f"/api/products/{pid}").status_code == 404


def test_stats_matches_ground_truth(client, repo):
    resp = client.get("/api/products/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    products = _all_products(repo)
    total = len(products)
    assert data["total"] == total
    # Money is compared with a two-cent tolerance to absorb a last-ulp sum
    # difference between the Python and Mongo backends.
    assert data["average_price"] == pytest.approx(sum(p.price for p in products) / total, abs=0.02)
    # products without a category are excluded from by_category
    assert sum(v["count"] for v in data["by_category"].values()) <= total

    groups: dict[str, list] = {}
    for p in products:
        if p.category is None:
            continue
        groups.setdefault(p.category.name, []).append(p)
    assert set(data["by_category"].keys()) == set(groups)
    for cat, vals in data["by_category"].items():
        g = groups[cat]
        assert vals["count"] == len(g)
        assert vals["average_price"] == pytest.approx(sum(p.price for p in g) / len(g), abs=0.02)
        assert vals["total_value"] == pytest.approx(sum(p.price * p.stock for p in g), abs=0.02)


def test_meta_backend(client, repo, test_products):
    resp = client.get("/meta")
    assert resp.status_code == 200
    data = resp.get_json()
    expected_backend = "mongo" if type(repo).__module__ == "mongo_repository" else "memory"
    assert data["backend"] == expected_backend
    assert data["product_count"] == len(test_products)


def test_list_categories(client):
    resp = client.get("/api/categories")
    assert resp.status_code == 200
    items = resp.get_json()
    assert isinstance(items, list) and items
    assert all(set(i.keys()) == {"id", "name"} for i in items)
    assert [i["name"] for i in items] == sorted(i["name"] for i in items)
    assert len({i["id"] for i in items}) == len(items)


def test_list_brands(client):
    resp = client.get("/api/brands")
    assert resp.status_code == 200
    items = resp.get_json()
    assert isinstance(items, list) and items
    assert all(set(i.keys()) == {"id", "name"} for i in items)
    assert [i["name"] for i in items] == sorted(i["name"] for i in items)
    assert len({i["id"] for i in items}) == len(items)


def _first_category(client):
    items = client.get("/api/categories").get_json()
    return items[0]["id"], items[0]["name"]


def test_rename_category(client):
    cid, _ = _first_category(client)
    resp = client.put(f"/api/categories/{cid}", json={"name": "Renamed Cat"})
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "Renamed Cat"


def test_rename_category_id_is_stable_in_memory(client, backend):
    # id stability across a rename is an in-memory reference detail: a
    # denormalized implementation may re-issue the id on rename.
    if backend != "memory":
        pytest.skip("id stability is specific to the in-memory reference")
    cid, _ = _first_category(client)
    resp = client.put(f"/api/categories/{cid}", json={"name": "Renamed Cat"})
    assert resp.status_code == 200
    assert resp.get_json()["id"] == cid


def test_rename_category_same_name_is_idempotent(client):
    cid, name = _first_category(client)
    resp = client.put(f"/api/categories/{cid}", json={"name": name})
    assert resp.status_code == 200
    assert resp.get_json() == {"id": cid, "name": name}


def test_rename_category_extra_json_keys_are_ignored(client):
    cid, name = _first_category(client)
    resp = client.put(f"/api/categories/{cid}", json={"name": name, "junk": 123, "another": "x"})
    assert resp.status_code == 200
    assert resp.get_json()["name"] == name


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (["not", "a", "dict"], "body must be a JSON object"),
        ({}, "name is required"),
        ({"name": 42}, "name must be a string"),
    ],
    ids=["non-dict", "missing-name", "non-string-name"],
)
def test_rename_category_invalid_bodies_400(client, body, error):
    cid, _ = _first_category(client)
    resp = client.put(f"/api/categories/{cid}", json=body)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": error}


def test_rename_category_malformed_json_400(client):
    cid, _ = _first_category(client)
    resp = client.put(f"/api/categories/{cid}", data="{bad", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "invalid JSON body"}


def test_rename_category_unknown_id_404(client):
    resp = client.put("/api/categories/no-such-id", json={"name": "X"})
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


def test_delete_category_reports_affected_products(client):
    created = client.post(
        "/api/products", json={"name": "DelCat", "category": "DelCatOnly", "price": 1.0}
    ).get_json()
    resp = client.delete(f"/api/categories/{created['category']['id']}")
    assert resp.status_code == 200
    assert resp.get_json() == {"removed_from": 1}
    got = client.get(f"/api/products/{created['id']}").get_json()
    assert got["category"] is None  # the product survives, detached


def test_delete_category_unknown_404(client):
    resp = client.delete("/api/categories/no-such-id")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


def test_rename_brand(client):
    items = client.get("/api/brands").get_json()
    assert items
    resp = client.put(f"/api/brands/{items[0]['id']}", json={"name": "Renamed Brand"})
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "Renamed Brand"


def test_rename_brand_id_is_stable_in_memory(client, backend):
    # id stability across a rename is an in-memory reference detail: a
    # denormalized implementation may re-issue the id on rename.
    if backend != "memory":
        pytest.skip("id stability is specific to the in-memory reference")
    items = client.get("/api/brands").get_json()
    resp = client.put(f"/api/brands/{items[0]['id']}", json={"name": "Renamed Brand"})
    assert resp.status_code == 200
    assert resp.get_json()["id"] == items[0]["id"]


def test_delete_brand_reports_affected_products(client):
    created = client.post(
        "/api/products", json={"name": "DelBrand", "brand": "DelBrandOnly", "price": 1.0}
    ).get_json()
    resp = client.delete(f"/api/brands/{created['brand']['id']}")
    assert resp.status_code == 200
    assert resp.get_json() == {"removed_from": 1}
    assert client.get(f"/api/products/{created['id']}").get_json()["brand"] is None


@pytest.mark.parametrize("path", ["/", "/categories", "/brands"])
def test_timing_footer_present_on_html_pages(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert "text/html" in resp.content_type
    html = resp.get_data(as_text=True)
    assert "</body>" in html  # the anchor every page must keep
    # FR decimal comma, injected right before the final </body>
    assert re.search(r"Générée en \d+,\d ms</footer></body>", html)


@pytest.mark.parametrize(
    "path", ["/api/products", "/api/products/stats", "/api/categories", "/api/brands", "/meta"]
)
def test_timing_footer_absent_from_json(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "page-timing" not in body
    assert "Générée en" not in body


def test_ui_entity_routes_serve_pages(client):
    for path in ("/categories", "/brands"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "text/html" in resp.content_type


def test_ui_index_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.content_type
    assert "Catalogue produits" in resp.get_data(as_text=True)


@pytest.mark.parametrize(
    "path",
    [
        "/static/index.html",
        "/static/app.js",
        "/static/style.css",
        "/static/categories.html",
        "/static/categories.js",
        "/static/brands.html",
        "/static/brands.js",
    ],
)
def test_ui_static_assets_served(client, path):
    assert client.get(path).status_code == 200
