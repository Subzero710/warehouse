"""MongoDB product repository with embedded category and brand data."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import replace

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError

from models import (
    Brand,
    CatalogStats,
    Category,
    CategoryStats,
    Product,
    ProductPage,
    ProductQuery,
)
from repository import EntityNotFoundError, ProductRepository

LOGGER = logging.getLogger(__name__)

# The Mongo collection that holds products. A neutral contract detail: the
# test fixtures and seeder import this to know where to clean up. Deliberately
# NOT a performance hint (no mention of indexes, $text, keyset, projections).
PRODUCT_COLLECTION_NAME = "product"
CATEGORY_COLLECTION_NAME = "category"
BRAND_COLLECTION_NAME = "brand"


class MongoProductRepository(ProductRepository):
    """MongoDB-backed implementation of the shared repository contract."""

    def __init__(self, uri: str, db_name: str) -> None:
        self._uri = uri
        self._db_name = db_name
        # Lazy client: no connection is opened until an operation actually runs.
        self._client = MongoClient(uri)
        self._db = self._client[db_name]
        self._products = self._db[PRODUCT_COLLECTION_NAME]
        self._categories = self._db[CATEGORY_COLLECTION_NAME]
        self._brands = self._db[BRAND_COLLECTION_NAME]
        self._category_names = self._db["category_names"]
        self._brand_names = self._db["brand_names"]

    def close(self) -> None:
        """Release backend resources. Called once at shutdown."""
        LOGGER.info("close()")
        self._client.close()

    def _get_or_create_entity(self, name: str, kind: str) -> Category | Brand:
        if kind == "category":
            collection, names, factory = self._categories, self._category_names, Category
        else:
            collection, names, factory = self._brands, self._brand_names, Brand

        record = names.find_one({"_id": name}, {"entity_id": 1})
        if record is not None:
            return factory(id=record["entity_id"], name=name)

        entity_id = uuid.uuid4().hex
        collection.insert_one({"_id": entity_id, "name": name})
        try:
            # _id is unique: two simultaneous creates cannot bind one name
            # to two different entities.
            names.insert_one({"_id": name, "entity_id": entity_id})
        except DuplicateKeyError:
            collection.delete_one({"_id": entity_id})
            existing = names.find_one({"_id": name}, {"entity_id": 1})
            return factory(id=existing["entity_id"], name=name)
        return factory(id=entity_id, name=name)

    @staticmethod
    def _document(product: Product, product_id: str, category, brand) -> dict:
        return {
            "_id": product_id,
            "name": product.name,
            "description": product.description,
            "category": (
                {"id": category.id, "name": category.name}
                if category is not None else None
            ),
            "brand": (
                {"id": brand.id, "name": brand.name}
                if brand is not None else None
            ),
            "price": product.price,
            "stock": product.stock,
            "rating": product.rating,
            "units_sold": product.units_sold,
            "created_at": product.created_at,
        }

    @staticmethod
    def _product_from_document(doc: dict) -> Product:
        category_data = doc["category"]
        brand_data = doc["brand"]
        category = Category(**category_data) if category_data is not None else None
        brand = Brand(**brand_data) if brand_data is not None else None
        return Product(
            id=doc["_id"],
            name=doc["name"],
            description=doc["description"],
            category=category,
            brand=brand,
            price=doc["price"],
            stock=doc["stock"],
            rating=doc["rating"],
            units_sold=doc["units_sold"],
            created_at=doc["created_at"],
        )

    def create_product(self, product: Product) -> Product:
        """Create a single product.

        The repository generates the product ``id`` (the placeholder carried
        on ``product`` is replaced); a referenced category / brand is
        auto-created on first sight of its name. Returns the stored product.
        """
        LOGGER.info("create_product(%r, %s, %d)", product.name, product.price, product.stock)
        category = (
            self._get_or_create_entity(product.category.name, "category")
            if product.category is not None else None
        )
        brand = (
            self._get_or_create_entity(product.brand.name, "brand")
            if product.brand is not None else None
        )
        product_id = uuid.uuid4().hex
        self._products.insert_one(self._document(product, product_id, category, brand))
        return replace(product, id=product_id, category=category, brand=brand)

    def create_products(self, products: list[Product]) -> list[Product]:
        """Insert many products in one bulk call (the seed path).

        Each product ``id`` is generated by the repository, as in
        ``create_product``. The referenced entities are created ONCE per name
        for the whole batch. Returns the stored products in input order; an
        empty input returns an empty list.
        """
        LOGGER.info("create_products(%d)", len(products))
        if not products:
            return []

        # One entity creation/lookup per distinct name in this entire batch;
        # one MongoDB insert_many call for the actual products.
        categories = {
            name: self._get_or_create_entity(name, "category")
            for name in dict.fromkeys(
                p.category.name for p in products if p.category is not None
            )
        }
        brands = {
            name: self._get_or_create_entity(name, "brand")
            for name in dict.fromkeys(
                p.brand.name for p in products if p.brand is not None
            )
        }
        stored = []
        documents = []
        for product in products:
            category = categories[product.category.name] if product.category is not None else None
            brand = brands[product.brand.name] if product.brand is not None else None
            product_id = uuid.uuid4().hex
            documents.append(self._document(product, product_id, category, brand))
            stored.append(replace(product, id=product_id, category=category, brand=brand))
        self._products.insert_many(documents)
        return stored

    def get_product(self, product_id: str) -> Product | None:
        """Return the product with ``product_id`` (entities materialized at fetch time).

        Never raises: an unknown or malformed id yields ``None``.
        """
        LOGGER.info("get_product(%r)", product_id)
        doc = self._products.find_one({"_id": product_id})
        return self._product_from_document(doc) if doc is not None else None

    def update_product(self, product: Product) -> Product:
        """Fully replace the mutable fields of the stored product targeted by ``product.id``.

        An unknown or malformed ``product.id`` raises
        ``repository.EntityNotFoundError``. The argument ``created_at`` is
        IGNORED: the stored value is kept. ``category`` / ``brand`` are
        re-bound BY NAME: the argument entity ids are ignored, an unknown name
        is auto-created, and ``None`` detaches the product from that entity.
        The repository does NOT re-validate the fields: it trusts the typed
        product. Returns the updated product.
        """
        LOGGER.info("update_product(%r, %r)", product.id, product.name)
        existing = self._products.find_one({"_id": product.id}, {"created_at": 1})
        if existing is None:
            raise EntityNotFoundError(f"product id {product.id!r} not found")

        category = (
            self._get_or_create_entity(product.category.name, "category")
            if product.category is not None else None
        )
        brand = (
            self._get_or_create_entity(product.brand.name, "brand")
            if product.brand is not None else None
        )
        mutable = self._document(product, product.id, category, brand)
        del mutable["_id"]
        del mutable["created_at"]  # never overwrite the stored creation date
        result = self._products.update_one({"_id": product.id}, {"$set": mutable})
        if not result.matched_count:
            raise EntityNotFoundError(f"product id {product.id!r} not found")
        return replace(
            product, created_at=existing["created_at"], category=category, brand=brand
        )

    def list_products(self, query: ProductQuery) -> ProductPage:
        """Filter, sort and paginate products according to ``query``.

        ``price_min`` / ``price_max`` are inclusive; the ``category`` /
        ``brand`` filters are by NAME (exact, case-sensitive);
        ``search_text`` is a case-insensitive substring match on the product
        name or description. Ties on ``sort_field`` are broken by ``id`` ascending.
        """
        LOGGER.info("list_products(%r)", query)
        conditions = []
        if query.category is not None:
            conditions.append({"category.name": query.category})
        if query.brand is not None:
            conditions.append({"brand.name": query.brand})
        if query.price_min is not None or query.price_max is not None:
            bounds = {}
            if query.price_min is not None:
                bounds["$gte"] = query.price_min
            if query.price_max is not None:
                bounds["$lte"] = query.price_max
            conditions.append({"price": bounds})
        if query.min_rating is not None:
            conditions.append({"rating": {"$gte": query.min_rating}})
        if query.in_stock is True:
            conditions.append({"stock": {"$gt": 0}})
        elif query.in_stock is False:
            conditions.append({"stock": 0})
        if query.search_text is not None:
            pattern = re.escape(query.search_text)
            conditions.append({"$or": [
                {"name": {"$regex": pattern, "$options": "i"}},
                {"description": {"$regex": pattern, "$options": "i"}},
            ]})

        predicate = {"$and": conditions} if conditions else {}
        total = self._products.count_documents(predicate)
        page = max(1, query.page)
        page_size = max(1, min(100, query.page_size))
        total_pages = (total + page_size - 1) // page_size if total else 0
        direction = ASCENDING if query.sort_ascending else DESCENDING
        cursor = (
            self._products.find(predicate)
            .sort([(query.sort_field, direction), ("_id", ASCENDING)])
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        items = [self._product_from_document(doc) for doc in cursor]
        return ProductPage(
            items=items, total=total, page=page,
            page_size=page_size, total_pages=total_pages,
        )

    def delete_product(self, product_id: str) -> None:
        """Delete the product with ``product_id``; returns ``None``.

        An unknown or malformed id raises ``repository.EntityNotFoundError``.
        """
        LOGGER.info("delete_product(%r)", product_id)
        result = self._products.delete_one({"_id": product_id})
        if not result.deleted_count:
            raise EntityNotFoundError(f"product id {product_id!r} not found")

    def get_stats(self) -> CatalogStats:
        """Return whole-catalog statistics.

        ``total`` is the number of products in the catalog: a plain count of
        stored products, not a total price, not a total stock, and not a
        count of distinct product names. ``by_category`` contains ONLY
        products that HAVE a category, keyed by the current category name and
        sorted by codepoint order; uncategorized products are excluded from
        ``by_category`` (they still count in ``total`` / ``average_price``).
        """
        LOGGER.info("get_stats()")
        result = next(self._products.aggregate([{"$facet": {
            "overall": [{"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "average_price": {"$avg": "$price"},
            }}],
            "categories": [
                {"$match": {"category.name": {"$type": "string"}}},
                {"$group": {
                    "_id": "$category.name",
                    "count": {"$sum": 1},
                    "sum_price": {"$sum": "$price"},
                    "total_value": {"$sum": {"$multiply": ["$price", "$stock"]}},
                }},
                {"$project": {
                    "count": 1,
                    "average_price": {"$divide": ["$sum_price", "$count"]},
                    "total_value": 1,
                }},
                {"$sort": {"_id": 1}},
            ],
        }}]), None)
        overall = result["overall"]
        if not overall:
            return CatalogStats(total=0, average_price=0.0, by_category={})
        categories = {
            row["_id"]: CategoryStats(
                count=row["count"],
                average_price=row["average_price"],
                total_value=row["total_value"],
            )
            for row in result["categories"]
        }
        return CatalogStats(
            total=overall[0]["total"],
            average_price=overall[0]["average_price"],
            by_category=categories,
        )

    def ensure_indexes(self) -> None:
        """Ensure backend indexes exist. Idempotent."""
        LOGGER.info("ensure_indexes()")
        # Every tie must be ordered by _id ascending, including when sorting
        # the primary field descending. Separate index directions are needed.
        for field in ("name", "price", "rating", "units_sold", "created_at"):
            for direction in (ASCENDING, DESCENDING):
                self._products.create_index([(field, direction), ("_id", ASCENDING)])
        self._products.create_index([("category.name", ASCENDING)])
        self._products.create_index([("brand.name", ASCENDING)])
        self._products.create_index([("category.id", ASCENDING)])
        self._products.create_index([("brand.id", ASCENDING)])
        self._categories.create_index([("name", ASCENDING)])
        self._brands.create_index([("name", ASCENDING)])
        # category_names/brand_names already have unique _id indexes.

    def _list_entities(self, kind: str) -> list[Category] | list[Brand]:
        collection = self._categories if kind == "category" else self._brands
        factory = Category if kind == "category" else Brand
        # _id sorts ties deterministically; the name sort matches codepoint
        # order under MongoDB's default simple collation.
        return [
            factory(id=row["_id"], name=row["name"])
            for row in collection.find({}, {"name": 1}).sort([("name", 1), ("_id", 1)])
        ]

    def _get_entity(self, kind: str, entity_id: str) -> Category | Brand | None:
        collection = self._categories if kind == "category" else self._brands
        factory = Category if kind == "category" else Brand
        doc = collection.find_one({"_id": entity_id})
        return factory(id=doc["_id"], name=doc["name"]) if doc is not None else None

    def _rename_entity(self, kind: str, entity_id: str, new_name: str) -> Category | Brand:
        collection = self._categories if kind == "category" else self._brands
        names = self._category_names if kind == "category" else self._brand_names
        factory = Category if kind == "category" else Brand
        current = collection.find_one({"_id": entity_id})
        if current is None:
            raise EntityNotFoundError(f"{kind} id {entity_id!r} not found")
        trimmed = new_name.strip()
        if trimmed == current["name"]:
            return factory(id=entity_id, name=trimmed)
        collection.update_one({"_id": entity_id}, {"$set": {"name": trimmed}})
        names.delete_one({"_id": current["name"], "entity_id": entity_id})
        names.replace_one(
            {"_id": trimmed}, {"_id": trimmed, "entity_id": entity_id}, upsert=True
        )
        self._products.update_many(
            {f"{kind}.id": entity_id}, {"$set": {f"{kind}.name": trimmed}}
        )
        return factory(id=entity_id, name=trimmed)

    def _delete_entity(self, kind: str, entity_id: str) -> int:
        collection = self._categories if kind == "category" else self._brands
        names = self._category_names if kind == "category" else self._brand_names
        current = collection.find_one({"_id": entity_id})
        if current is None:
            raise EntityNotFoundError(f"{kind} id {entity_id!r} not found")
        affected = self._products.update_many(
            {f"{kind}.id": entity_id}, {"$set": {kind: None}}
        ).modified_count
        collection.delete_one({"_id": entity_id})
        names.delete_one({"_id": current["name"], "entity_id": entity_id})
        return affected

    def list_categories(self) -> list[Category]:
        """Return every category, sorted by name ascending (codepoint order).

        Categories are created implicitly when products are saved with an
        unknown name.
        """
        LOGGER.info("list_categories()")
        return self._list_entities("category")

    def get_category(self, category_id: str) -> Category | None:
        """Return the category with ``category_id`` or ``None``.

        Never raises: an unknown or malformed id yields ``None``. Categories
        are created implicitly when products are saved with an unknown name.
        """
        LOGGER.info("get_category(%r)", category_id)
        return self._get_entity("category", category_id)

    def rename_category(self, category_id: str, new_name: str) -> Category:
        """Rename the category; ``new_name`` is trimmed.

        An unknown or malformed ``category_id`` raises
        ``repository.EntityNotFoundError``; a name identical to the current
        one (case-sensitive) is a no-op returning the entity. Whether the
        ``id`` is preserved across a rename is implementation-defined (see
        the ``Category.id`` / ``Brand.id`` docstrings); every product
        referencing the category carries the new name at the next fetch.
        Returns the renamed entity.
        """
        LOGGER.info("rename_category(%r, %r)", category_id, new_name)
        return self._rename_entity("category", category_id, new_name)

    def delete_category(self, category_id: str) -> int:
        """Delete the category; returns the number of products affected.

        Every product that carried the category is detached (its ``category``
        becomes ``None``; the product survives, nothing is deleted on the
        product side). An unknown or malformed id raises
        ``repository.EntityNotFoundError``. The count is 0 for an orphaned
        category.
        """
        LOGGER.info("delete_category(%r)", category_id)
        return self._delete_entity("category", category_id)

    def list_brands(self) -> list[Brand]:
        """Return every brand, sorted by name ascending (codepoint order).

        Brands are created implicitly when products are saved with an unknown
        name.
        """
        LOGGER.info("list_brands()")
        return self._list_entities("brand")

    def get_brand(self, brand_id: str) -> Brand | None:
        """Return the brand with ``brand_id`` or ``None``.

        Never raises: an unknown or malformed id yields ``None``. Brands are
        created implicitly when products are saved with an unknown name.
        """
        LOGGER.info("get_brand(%r)", brand_id)
        return self._get_entity("brand", brand_id)

    def rename_brand(self, brand_id: str, new_name: str) -> Brand:
        """Rename the brand; ``new_name`` is trimmed.

        An unknown or malformed ``brand_id`` raises
        ``repository.EntityNotFoundError``; a name identical to the current
        one (case-sensitive) is a no-op returning the entity. Whether the
        ``id`` is preserved across a rename is implementation-defined (see
        the ``Category.id`` / ``Brand.id`` docstrings); every product
        referencing the brand carries the new name at the next fetch. Returns
        the renamed entity.
        """
        LOGGER.info("rename_brand(%r, %r)", brand_id, new_name)
        return self._rename_entity("brand", brand_id, new_name)

    def delete_brand(self, brand_id: str) -> int:
        """Delete the brand; returns the number of products affected.

        Every product that carried the brand is detached (its ``brand``
        becomes ``None``; the product survives, nothing is deleted on the
        product side). An unknown or malformed id raises
        ``repository.EntityNotFoundError``. The count is 0 for an orphaned
        brand.
        """
        LOGGER.info("delete_brand(%r)", brand_id)
        return self._delete_entity("brand", brand_id)
