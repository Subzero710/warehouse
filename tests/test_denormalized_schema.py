"""MongoDB schema tests."""

import pytest


@pytest.fixture
def mongo_repo(repo, backend):
    if backend != "mongo":
        pytest.skip("MongoDB storage format only")
    return repo


def test_product_stores_category_and_brand_as_strings(mongo_repo):
    doc = mongo_repo._products.find_one({"name": "Zebra42 Widget"})
    assert doc["category"] == "Books"
    assert doc["brand"] == "Acme"
    assert "category_id" not in doc and "brand_id" not in doc
    empty = mongo_repo._products.find_one({"name": "Naked Gadget"})
    assert empty["category"] is None and empty["brand"] is None
    assert mongo_repo._db.list_collection_names() == ["product"]


def test_rename_and_delete_update_product_strings(mongo_repo):
    product = mongo_repo._products.find_one({"name": "Zebra42 Widget"})
    category = next(c for c in mongo_repo.list_categories() if c.name == "Books")
    brand = next(b for b in mongo_repo.list_brands() if b.name == "Acme")
    renamed_cat = mongo_repo.rename_category(category.id, "Literature")
    renamed_brand = mongo_repo.rename_brand(brand.id, "Acme Corp")
    assert mongo_repo.get_category(renamed_cat.id).name == "Literature"
    assert mongo_repo.get_brand(renamed_brand.id).name == "Acme Corp"
    updated = mongo_repo._products.find_one({"_id": product["_id"]})
    assert updated["category"] == "Literature"
    assert updated["brand"] == "Acme Corp"
    mongo_repo.delete_category(renamed_cat.id)
    mongo_repo.delete_brand(renamed_brand.id)
    detached = mongo_repo._products.find_one({"_id": product["_id"]})
    assert detached["category"] is None
    assert detached["brand"] is None


def test_scalar_indexes(mongo_repo):
    mongo_repo.ensure_indexes()
    indexes = mongo_repo._products.index_information()
    keys = [index["key"] for index in indexes.values()]
    assert [("category", 1)] in keys
    assert [("brand", 1)] in keys
    assert mongo_repo._db.list_collection_names() == ["product"]
