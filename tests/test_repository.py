"""Repository tests, parameterized over both backends (memory + mongo)."""

from __future__ import annotations

import contextlib
import math
from datetime import datetime

import pytest

from models import Brand, Category, Product, ProductQuery
from repository import EntityNotFoundError


def _payload(name: str) -> dict:
    return {
        "name": name,
        "description": "a description",
        "category": "Books",
        "brand": "Acme",
        "price": 10.0,
        "stock": 5,
        "rating": 4.0,
        "units_sold": 3,
    }


def _to_product(product_id: str, payload: dict, created_at: datetime | None = None) -> Product:
    """Build a typed ``update_product`` argument from a mutable-field payload.

    The argument ``created_at`` defaults to a DISTINCTIVE fixed date, so an
    implementation that wrongly adopted the argument value couldn't pass the
    preservation tests.
    """
    normalized = Product.validate_payload(payload)
    return Product(id=product_id, created_at=created_at or datetime(2026, 2, 1), **normalized)


def _assert_tiebreak(items: list[Product], field: str) -> None:
    """Among consecutive items with equal ``field``, id must be ascending."""
    for i in range(len(items) - 1):
        a, b = items[i], items[i + 1]
        if getattr(a, field) == getattr(b, field):
            assert a.id < b.id, f"tie on {field} not broken by id ascending"


def test_create_product_generates_id_and_round_trips(repo):
    created = repo.create_product(Product.from_dict(_payload("New Thing")))
    assert created.id  # generated, non-empty
    assert created.id != ""
    fetched = repo.get_product(created.id)
    assert fetched is not None
    assert fetched == created


def test_create_product_generates_unique_ids(repo):
    a = repo.create_product(Product.from_dict(_payload("A")))
    b = repo.create_product(Product.from_dict(_payload("B")))
    assert a.id != b.id


def test_create_products_count_unique_retrievable(repo):
    before = repo.get_stats().total
    products = [Product.from_dict(_payload(f"P{i}")) for i in range(10)]
    created = repo.create_products(products)
    assert len(created) == 10
    # entities are created once per name for the whole batch: one shared id
    assert len({p.category.id for p in created}) == 1
    after = repo.get_stats().total
    assert after == before + 10
    all_ids = [p.id for p in repo.list_products(ProductQuery(page_size=100)).items]
    assert len(all_ids) == after
    assert len(set(all_ids)) == after
    assert all(all_ids)
    for pid in all_ids:
        assert repo.get_product(pid) is not None


def test_create_products_empty_input_returns_empty_list(repo):
    assert repo.create_products([]) == []


def test_get_product_existing(repo):
    created = repo.create_product(Product.from_dict(_payload("GetMe")))
    fetched = repo.get_product(created.id)
    assert fetched is not None
    assert fetched == created


def test_get_product_missing(repo):
    assert repo.get_product("missing-id-xyz") is None


def test_get_product_malformed_id_returns_none(repo):
    assert repo.get_product("%%%not-a-real-id@@@") is None
    assert repo.get_product("") is None


def test_update_product_changes_fields_and_round_trips(repo):
    created = repo.create_product(Product.from_dict(_payload("Before")))
    payload = {
        "name": "After",
        "description": "updated description",
        "category": "Toys",
        "brand": "Globex",
        "price": 42.5,
        "stock": 7,
        "rating": 4.9,
        "units_sold": 123,
    }
    updated = repo.update_product(_to_product(created.id, payload))
    fetched = repo.get_product(created.id)
    assert fetched is not None
    assert fetched == updated
    assert fetched.name == "After"
    assert fetched.description == "updated description"
    assert fetched.category is not None and fetched.category.name == "Toys"
    assert fetched.brand is not None and fetched.brand.name == "Globex"
    assert fetched.price == 42.5
    assert fetched.stock == 7
    assert fetched.rating == 4.9
    assert fetched.units_sold == 123


def test_update_product_preserves_id_and_created_at(repo):
    # A fixed, clearly-not-now created_at: a buggy "reset created_at to now
    # on update" implementation cannot pass this test.
    fixed_dt = datetime(2020, 1, 1, 12, 0, 0)
    product = Product(
        id="",
        name="PreserveMe",
        description="a description",
        category=Category(id=None, name="Books"),
        brand=Brand(id=None, name="Acme"),
        price=10.0,
        stock=5,
        rating=4.0,
        units_sold=3,
        created_at=fixed_dt,
    )
    created = repo.create_product(product)
    assert created.created_at == fixed_dt  # create preserves the passed value
    # The update argument carries a DISTINCTIVE created_at: it must be
    # ignored, the stored value kept.
    updated = repo.update_product(_to_product(created.id, _payload("PreserveMe2")))
    assert updated.id == created.id
    assert updated.created_at == fixed_dt
    fetched = repo.get_product(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.created_at == fixed_dt


def test_update_product_unknown_id_raises(repo):
    with pytest.raises(EntityNotFoundError):
        repo.update_product(_to_product("no-such-id", _payload("Ghost")))


def test_update_product_malformed_id_raises(repo):
    for bad in ("%%%not-a-real-id@@@", ""):
        with pytest.raises(EntityNotFoundError):
            repo.update_product(_to_product(bad, _payload("Ghost")))


def test_update_product_trusts_the_typed_product(repo):
    # The repository does NOT re-validate: it trusts the typed product.
    # Boundary validation belongs to the entry points that build the product.
    created = repo.create_product(Product.from_dict(_payload("Base")))
    evil = Product(
        id=created.id,
        name="Base",
        description="a description",
        category=None,
        brand=None,
        price=-3.5,
        stock=0,
        rating=0.0,
        units_sold=0,
        created_at=datetime(2026, 2, 1),
    )
    updated = repo.update_product(evil)
    assert updated.price == -3.5


def test_update_product_rebinds_entities_by_name_and_detaches(repo):
    created = repo.create_product(
        Product.from_dict({"name": "Bind", "category": "Books", "brand": "Acme", "price": 1.0})
    )
    forged = Product(
        id=created.id,
        name="Bind",
        description="a description",
        category=Category(id="FORGED", name="NewShelf"),  # argument id ignored
        brand=None,  # detaches
        price=2.0,
        stock=1,
        rating=0.0,
        units_sold=0,
        created_at=datetime(2026, 2, 1),
    )
    updated = repo.update_product(forged)
    assert updated.category is not None
    assert updated.category.name == "NewShelf"
    assert updated.category.id != "FORGED"  # re-bound by name, auto-created
    assert updated.brand is None


def test_list_products_unfiltered(repo, test_products):
    page = repo.list_products(ProductQuery())
    assert page.total == len(test_products)
    assert page.page_size == 20  # default
    assert len(page.items) == 20  # first page of 26
    assert page.total_pages == math.ceil(len(test_products) / 20)
    # default sort: created_at descending
    created = [p.created_at for p in page.items]
    assert created == sorted(created, reverse=True)
    latest = max(test_products, key=lambda p: p.created_at)
    assert page.items[0].name == latest.name


def test_list_products_filter_category(repo, test_products):
    page = repo.list_products(ProductQuery(category="Books", page_size=100))
    expected = [p for p in test_products if p.category is not None and p.category.name == "Books"]
    assert page.total == len(expected)
    assert all(p.category is not None and p.category.name == "Books" for p in page.items)


def test_list_products_price_range_inclusive(repo, test_products):
    page = repo.list_products(ProductQuery(price_min=100.0, page_size=100))
    expected = [p for p in test_products if p.price >= 100.0]
    assert page.total == len(expected)
    assert any(p.price == 100.0 for p in page.items)

    page_max = repo.list_products(ProductQuery(price_max=100.0, page_size=100))
    expected_max = [p for p in test_products if p.price <= 100.0]
    assert page_max.total == len(expected_max)
    assert any(p.price == 100.0 for p in page_max.items)


def test_list_products_min_rating_inclusive(repo, test_products):
    page = repo.list_products(ProductQuery(min_rating=4.0, page_size=100))
    expected = [p for p in test_products if p.rating >= 4.0]
    assert page.total == len(expected)
    assert any(p.rating == 4.0 for p in page.items)


def test_list_products_in_stock_true(repo, test_products):
    page = repo.list_products(ProductQuery(in_stock=True, page_size=100))
    expected = [p for p in test_products if p.stock > 0]
    assert page.total == len(expected)
    assert all(p.stock > 0 for p in page.items)


def test_list_products_in_stock_false(repo, test_products):
    page = repo.list_products(ProductQuery(in_stock=False, page_size=100))
    expected = [p for p in test_products if p.stock == 0]
    assert len(expected) >= 1
    assert page.total == len(expected)
    assert all(p.stock == 0 for p in page.items)


def test_list_products_combined_filters(repo, test_products):
    page = repo.list_products(
        ProductQuery(
            category="Books", price_min=40.0, price_max=100.0, min_rating=3.5, page_size=100
        )
    )
    expected = [
        p
        for p in test_products
        if p.category is not None
        and p.category.name == "Books"
        and 40.0 <= p.price <= 100.0
        and p.rating >= 3.5
    ]
    assert len(expected) >= 1
    assert page.total == len(expected)


def test_list_products_sort_price_asc(repo):
    page = repo.list_products(ProductQuery(sort_field="price", sort_ascending=True, page_size=100))
    prices = [p.price for p in page.items]
    assert prices == sorted(prices)
    _assert_tiebreak(page.items, "price")


def test_list_products_sort_rating_desc(repo):
    page = repo.list_products(
        ProductQuery(sort_field="rating", sort_ascending=False, page_size=100)
    )
    ratings = [p.rating for p in page.items]
    assert ratings == sorted(ratings, reverse=True)
    _assert_tiebreak(page.items, "rating")


def test_list_products_sort_name_asc(repo):
    page = repo.list_products(ProductQuery(sort_field="name", sort_ascending=True, page_size=100))
    names = [p.name for p in page.items]
    assert names == sorted(names)
    _assert_tiebreak(page.items, "name")


def test_list_products_search_name_only(repo):
    # "zulu" appears only in a name (Zulu Rope), never in a description.
    page = repo.list_products(ProductQuery(search_text="zulu", page_size=100))
    assert page.total >= 1
    assert any(p.name.lower() == "zulu rope" for p in page.items)
    assert all("zulu" not in p.description.lower() for p in page.items)


def test_list_products_search_description_only(repo):
    # "zebrawood" appears only in a description (Alpha Chair), never in a name:
    # proves the search reaches the description, not just the name.
    page = repo.list_products(ProductQuery(search_text="zebrawood", page_size=100))
    assert page.total >= 1
    assert any(p.description.lower() == "made of zebrawood" for p in page.items)
    assert all("zebrawood" not in p.name.lower() for p in page.items)


def test_list_products_search_zero_matches(repo):
    page = repo.list_products(ProductQuery(search_text="qqqzzznotthere", page_size=100))
    assert page.total == 0
    assert page.items == []


def test_list_products_search_combined_with_category(repo, test_products):
    page = repo.list_products(ProductQuery(search_text="chair", category="Toys", page_size=100))
    expected = [
        p
        for p in test_products
        if p.category is not None
        and p.category.name == "Toys"
        and ("chair" in p.name.lower() or "chair" in p.description.lower())
    ]
    assert len(expected) >= 1
    assert page.total == len(expected)
    assert all(p.category is not None and p.category.name == "Toys" for p in page.items)


def test_list_products_pagination_page2_disjoint_from_page1(repo):
    p1 = repo.list_products(ProductQuery(page=1, page_size=10))
    p2 = repo.list_products(ProductQuery(page=2, page_size=10))
    ids1 = {p.id for p in p1.items}
    ids2 = {p.id for p in p2.items}
    assert len(p1.items) == 10
    assert len(p2.items) == 10
    assert not (ids1 & ids2)


def test_list_products_pagination_last_page_short(repo, test_products):
    total = len(test_products)
    page_size = 10
    total_pages = math.ceil(total / page_size)
    last = repo.list_products(ProductQuery(page=total_pages, page_size=page_size))
    assert last.total_pages == total_pages
    assert len(last.items) == total - (total_pages - 1) * page_size


def test_list_products_pagination_beyond_last_page_empty(repo, test_products):
    page = repo.list_products(ProductQuery(page=999, page_size=10))
    assert page.items == []
    assert page.total == len(test_products)


def test_delete_product_existing(repo):
    created = repo.create_product(Product.from_dict(_payload("ToDelete")))
    before = repo.get_stats().total
    assert repo.delete_product(created.id) is None
    assert repo.get_product(created.id) is None
    assert repo.get_stats().total == before - 1


def test_delete_product_missing_raises(repo):
    with pytest.raises(EntityNotFoundError):
        repo.delete_product("no-such-id")


def test_delete_product_malformed_id_raises(repo):
    for bad in ("%%%not-a-real-id@@@", ""):
        with pytest.raises(EntityNotFoundError):
            repo.delete_product(bad)


def test_get_stats(repo, test_products):
    stats = repo.get_stats()
    assert stats.total == len(test_products)
    # products without a category are EXCLUDED from by_category
    assert sum(cs.count for cs in stats.by_category.values()) <= stats.total
    assert list(stats.by_category.keys()) == sorted(stats.by_category.keys())

    expected_avg = sum(p.price for p in test_products) / len(test_products)
    assert stats.average_price == pytest.approx(expected_avg)

    groups: dict[str, list[Product]] = {}
    for p in test_products:
        if p.category is None:
            continue
        groups.setdefault(p.category.name, []).append(p)
    assert set(stats.by_category.keys()) == set(groups)
    for cat, cs in stats.by_category.items():
        g = groups[cat]
        assert cs.count == len(g)
        assert cs.average_price == pytest.approx(sum(p.price for p in g) / len(g))
        assert cs.total_value == pytest.approx(sum(p.price * p.stock for p in g))


def test_list_categories_sorted_by_name(repo, test_products):
    categories = repo.list_categories()
    names = [c.name for c in categories]
    assert names == sorted(names)  # codepoint order
    assert all(c.id for c in categories)  # ids always populated
    assert set(names) == {p.category.name for p in test_products if p.category is not None}


def test_get_category_by_id(repo):
    categories = repo.list_categories()
    books = next(c for c in categories if c.name == "Books")
    assert repo.get_category(books.id) == books


def test_get_category_unknown_or_malformed_returns_none(repo):
    assert repo.get_category("") is None


def test_rename_category_updates_products_and_listing(repo):
    books = next(c for c in repo.list_categories() if c.name == "Books")
    # stored products (real ids) filtered by category name
    book_products = repo.list_products(ProductQuery(category="Books", page_size=100)).items
    assert book_products  # hand data invariant
    renamed = repo.rename_category(books.id, "Literature")
    assert renamed.name == "Literature"
    for p in book_products:
        fetched = repo.get_product(p.id)
        assert fetched is not None
        assert fetched.category is not None and fetched.category.name == "Literature"
    names = [c.name for c in repo.list_categories()]
    assert names.count("Literature") == 1
    assert "Books" not in names


def test_rename_category_id_is_stable_in_memory(repo, backend):
    # id stability across a rename is an in-memory reference detail: a
    # denormalized implementation may re-issue the id on rename.
    if backend != "memory":
        pytest.skip("id stability is specific to the in-memory reference")
    books = next(c for c in repo.list_categories() if c.name == "Books")
    renamed = repo.rename_category(books.id, "Literature")
    assert renamed.id == books.id  # the reference freezes the id at creation
    assert repo.get_category(books.id).name == "Literature"


def test_rename_category_same_name_is_a_noop(repo):
    books = next(c for c in repo.list_categories() if c.name == "Books")
    result = repo.rename_category(books.id, "Books")
    assert result.id == books.id and result.name == "Books"
    assert repo.get_category(books.id) == books


def test_rename_category_trims_the_new_name(repo):
    books = next(c for c in repo.list_categories() if c.name == "Books")
    renamed = repo.rename_category(books.id, "   Padded Name   ")
    assert renamed.name == "Padded Name"
    assert repo.get_category(renamed.id).name == "Padded Name"


def test_rename_category_unknown_id_raises(repo):
    with pytest.raises(EntityNotFoundError):
        repo.rename_category("no-such-id", "Whatever")
    with pytest.raises(EntityNotFoundError):
        repo.rename_category("%%%not-a-real-id@@@", "Whatever")


def test_rename_category_snapshot_keeps_the_old_entity(repo):
    # Snapshot semantics: a product snapshot taken BEFORE the rename keeps
    # the old entity; a fresh refetch carries the new name.
    snapshot = repo.list_products(ProductQuery(category="Books", page_size=1)).items[0]
    books = snapshot.category
    repo.rename_category(books.id, "Shelves")
    assert snapshot.category.name == "Books"  # the stale snapshot is the contract
    refetched = repo.get_product(snapshot.id)
    assert refetched is not None
    assert refetched.category.name == "Shelves"


def test_delete_category_detaches_products_and_counts(repo):
    books = next(c for c in repo.list_categories() if c.name == "Books")
    book_products = repo.list_products(ProductQuery(category="Books", page_size=100)).items
    total_before = repo.get_stats().total
    affected = repo.delete_category(books.id)
    assert affected == len(book_products)
    for p in book_products:
        fetched = repo.get_product(p.id)
        assert fetched is not None
        assert fetched.category is None
    assert repo.get_stats().total == total_before
    assert "Books" not in {c.name for c in repo.list_categories()}
    assert "Books" not in repo.get_stats().by_category


def test_delete_category_orphan_returns_zero(repo):
    created = repo.create_product(
        Product.from_dict({"name": "OrphanCarrier", "category": "OrphanOnly", "price": 1.0})
    )
    orphan = repo.get_category(created.category.id)
    assert orphan is not None
    # orphan it: the product is re-bound to another category (no entity given)
    rebind = _to_product(created.id, {"name": "OrphanCarrier", "description": "d", "price": 1.0})
    repo.update_product(rebind)
    with contextlib.suppress(EntityNotFoundError):  # acceptable too
        assert repo.delete_category(orphan.id) == 0


def test_delete_category_unknown_id_raises(repo):
    with pytest.raises(EntityNotFoundError):
        repo.delete_category("no-such-id")
    with pytest.raises(EntityNotFoundError):
        repo.delete_category("%%%not-a-real-id@@@")


def test_rename_brand_updates_products_and_listing(repo):
    acme = next(b for b in repo.list_brands() if b.name == "Acme")
    acme_products = repo.list_products(ProductQuery(brand="Acme", page_size=100)).items
    assert acme_products  # hand data invariant
    renamed = repo.rename_brand(acme.id, "Acme Corp")
    assert renamed.name == "Acme Corp"
    for p in acme_products:
        fetched = repo.get_product(p.id)
        assert fetched is not None
        assert fetched.brand is not None and fetched.brand.name == "Acme Corp"
    names = [b.name for b in repo.list_brands()]
    assert names.count("Acme Corp") == 1
    assert "Acme" not in names


def test_rename_brand_id_is_stable_in_memory(repo, backend):
    # id stability across a rename is an in-memory reference detail: a
    # denormalized implementation may re-issue the id on rename.
    if backend != "memory":
        pytest.skip("id stability is specific to the in-memory reference")
    acme = next(b for b in repo.list_brands() if b.name == "Acme")
    renamed = repo.rename_brand(acme.id, "Acme Corp")
    assert renamed.id == acme.id  # the reference freezes the id at creation
    assert repo.get_brand(acme.id).name == "Acme Corp"


def test_rename_brand_edge_cases(repo):
    acme = next(b for b in repo.list_brands() if b.name == "Acme")
    assert repo.rename_brand(acme.id, "Acme").name == "Acme"
    with pytest.raises(EntityNotFoundError):
        repo.rename_brand("no-such-id", "X")


def test_rename_brand_snapshot_keeps_the_old_entity(repo):
    snapshot = repo.list_products(ProductQuery(brand="Acme", page_size=1)).items[0]
    acme = snapshot.brand
    repo.rename_brand(acme.id, "Acme Corp")
    assert snapshot.brand.name == "Acme"  # the stale snapshot is the contract
    refetched = repo.get_product(snapshot.id)
    assert refetched is not None
    assert refetched.brand.name == "Acme Corp"


def test_delete_brand_detaches_products_and_counts(repo):
    acme = next(b for b in repo.list_brands() if b.name == "Acme")
    acme_products = repo.list_products(ProductQuery(brand="Acme", page_size=100)).items
    total_before = repo.get_stats().total
    affected = repo.delete_brand(acme.id)
    assert affected == len(acme_products)
    for p in acme_products:
        fetched = repo.get_product(p.id)
        assert fetched is not None
        assert fetched.brand is None
    assert repo.get_stats().total == total_before
    assert "Acme" not in {b.name for b in repo.list_brands()}


def test_delete_brand_orphan_returns_zero(repo):
    created = repo.create_product(
        Product.from_dict({"name": "OrphanCarrier2", "brand": "OrphanBrand", "price": 1.0})
    )
    orphan = repo.get_brand(created.brand.id)
    assert orphan is not None
    rebind = _to_product(created.id, {"name": "OrphanCarrier2", "description": "d", "price": 1.0})
    repo.update_product(rebind)
    with contextlib.suppress(EntityNotFoundError):  # acceptable too
        assert repo.delete_brand(orphan.id) == 0


def test_delete_brand_unknown_id_raises(repo):
    with pytest.raises(EntityNotFoundError):
        repo.delete_brand("no-such-id")
    with pytest.raises(EntityNotFoundError):
        repo.delete_brand("%%%not-a-real-id@@@")


def test_entity_ids_follow_the_slug_rule(repo, backend):
    # SLUG ids are an in-memory reference detail: this format is NEVER
    # asserted across backends.
    if backend != "memory":
        pytest.skip("slug ids are specific to the in-memory reference")
    a = repo.create_product(
        Product.from_dict({"name": "S1", "category": "A/B", "brand": "B/C", "price": 1.0})
    )
    assert a.category.id == "a-b"
    assert a.brand.id == "b-c"
    b = repo.create_product(
        Product.from_dict({"name": "S2", "category": "A-B", "price": 1.0})
    )
    assert b.category.id == "a-b-2"  # slug collision: -2, in creation order
    c = repo.create_product(
        Product.from_dict({"name": "S3", "category": "A/B", "price": 1.0})
    )
    assert c.category.id == "a-b"  # known name reuses its frozen id
    d = repo.create_product(
        Product.from_dict({"name": "S4", "category": "??? ???", "price": 1.0})
    )
    assert d.category.id.startswith("u-") and len(d.category.id) == 10  # empty slug fallback
    e = repo.create_product(
        Product.from_dict({"name": "S5", "brand": "Café", "price": 1.0})
    )
    assert e.brand.id == "caf"  # lowercase only, no unicode fold: é -> "-"
    # the hand data's known name resolves through the registry
    books = repo.get_category("books")
    assert books is not None and books.name == "Books"


def test_ensure_indexes_idempotent(repo):
    repo.ensure_indexes()
    repo.ensure_indexes()
