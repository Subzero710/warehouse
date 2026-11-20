"""Tests for embedded category and brand fields."""

import pytest


def test_raw_product_has_embedded_category_and_brand(repo, backend):
    if backend != "mongo":
        pytest.skip("MongoDB storage format only")
    doc = repo._products.find_one({"name": "Zebra42 Widget"})
    assert doc["category"] == {"id": doc["category"]["id"], "name": "Books"}
    assert doc["brand"] == {"id": doc["brand"]["id"], "name": "Acme"}
    assert "category_id" not in doc and "brand_id" not in doc
    empty = repo._products.find_one({"name": "Naked Gadget"})
    assert empty["category"] is None and empty["brand"] is None


def test_rename_and_delete_propagate_into_documents(repo, backend):
    if backend != "mongo":
        pytest.skip("MongoDB storage format only")
    product = repo._products.find_one({"name": "Zebra42 Widget"})
    category_id = product["category"]["id"]
    brand_id = product["brand"]["id"]
    repo.rename_category(category_id, "Literature")
    repo.rename_brand(brand_id, "Acme Corp")
    updated = repo._products.find_one({"_id": product["_id"]})
    assert updated["category"] == {"id": category_id, "name": "Literature"}
    assert updated["brand"] == {"id": brand_id, "name": "Acme Corp"}
    repo.delete_category(category_id)
    repo.delete_brand(brand_id)
    detached = repo._products.find_one({"_id": product["_id"]})
    assert detached["category"] is None
    assert detached["brand"] is None


def test_embedded_name_indexes_created(repo, backend):
    if backend != "mongo":
        pytest.skip("MongoDB storage format only")
    repo.ensure_indexes()
    indexes = repo._products.index_information()
    keys = [index["key"] for index in indexes.values()]
    assert [("category.name", 1)] in keys
    assert [("brand.name", 1)] in keys
