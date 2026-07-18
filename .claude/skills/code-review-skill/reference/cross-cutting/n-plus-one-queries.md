# N+1 Query Problem — Cross-Language Guide

> N+1 queries are the most common performance anti-pattern in ORM and database access layers. This document covers problem definition, detection methods, general solutions, and cross-language code examples.

## Table of Contents

- [Problem Definition](#problem-definition)
- [Performance Impact](#performance-impact)
- [Detection Methods](#detection-methods)
- [General Solutions](#general-solutions)
- [Language-Specific Implementations](#language-specific-implementations)
- [Review Checklist](#review-checklist)

---

## Problem Definition

N+1 queries occur when **one query fetches N records, then a loop triggers N additional queries** to load related data.

```
Request flow:
  1 query   → fetch N primary records
  N queries → one related-data query per primary record
  ─────────
  Total: 1 + N queries
```

### Harm

| Issue | Impact |
|------|------|
| **Linear query growth** | 100 records = 101 SQL statements, 1000 = 1001 |
| **Stacked network latency** | Each query incurs round-trip latency (RTT); N round trips >> 1 batch query |
| **Connection pool exhaustion** | Many queries consume database connections, slowing the entire application |
| **Hard to spot in development** | Dev datasets are small so N+1 is subtle; performance collapses in production with large data volumes |

---

## Performance Impact

### Scenario comparison: fetch 100 users and their orders

| Approach | SQL count | Latency (assume RTT=1ms) | Use case |
|------|----------|---------------------|---------|
| N+1 lazy load | 101 | ~101ms | Very small datasets |
| Eager loading (JOIN) | 1 | ~1ms | One-to-many, moderate data volume |
| Eager loading (IN) | 2 | ~2ms | Many-to-many, large datasets |
| DataLoader / batch | 2 | ~2ms | GraphQL / complex graph queries |

### SQL count comparison

```sql
-- ❌ N+1: 1 + 100 = 101 queries
SELECT * FROM users;                          -- 1 query
SELECT * FROM orders WHERE user_id = 1;       -- query 2
SELECT * FROM orders WHERE user_id = 2;       -- query 3
...
SELECT * FROM orders WHERE user_id = 100;     -- query 101

-- ✅ Batch: 2 queries
SELECT * FROM users;
SELECT * FROM orders WHERE user_id IN (1,2,...,100);
```

---

## Detection Methods

### 1. ORM SQL logging

Enable SQL logging and observe query counts in test or development environments:

```python
# Django
import logging
logging.getLogger('django.db.backends').setLevel(logging.DEBUG)

# SQLAlchemy
import logging
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
```

```java
// Spring Boot application.yml
spring:
  jpa:
    show-sql: true
    properties:
      hibernate.format_sql: true
```

```csharp
// EF Core
optionsBuilder.LogTo(Console.WriteLine, LogLevel.Information);
```

### 2. Query count assertions

Assert SQL query counts in tests:

```python
# Django: django-assert-num-queries
from django.test.utils import CaptureQueriesContext
from django.db import connection

with CaptureQueriesContext(connection) as ctx:
    list(User.objects.select_related("profile").all())
assert len(ctx) <= 2  # expect at most 2 queries
```

```java
// Hibernate: p6spy or datasource-proxy
// Count SQL executions in tests
assertThat(sqlCount).isLessThanOrEqualTo(2);
```

### 3. APM / database monitoring tools

- **Django Debug Toolbar** — shows SQL count and timing in real time
- **p6spy** (Java) — JDBC-layer interception, logs all SQL
- **MiniProfiler** (.NET) — inline SQL statistics on the page
- **DataDog / New Relic** — slow-query alerts in production

---

## General Solutions

### Option 1: Eager Loading (JOIN prefetch)

Fetch primary and related records in a single JOIN query. Suitable for one-to-one and one-to-many relationships.

### Option 2: Batch Fetching (IN clause)

Two queries: primary records + `WHERE id IN (...)` to batch-load related records. Suitable for many-to-many relationships and large datasets.

### Option 3: DataLoader Pattern

In GraphQL or complex graph-query scenarios, collect all required IDs and merge them into one batch query.

```
// DataLoader pseudocode
class DataLoader<K, V> {
    load(K key) → V         // register demand, do not query immediately
    loadAll([K]) → [V]      // merge into one batch query
}
```

### Option 4: Projection

Query only the fields you need to reduce data transfer:

```sql
-- ❌ Fetch all columns
SELECT * FROM users JOIN profiles ON ...

-- ✅ Project only required fields
SELECT u.name, p.avatar_url FROM users u JOIN profiles p ON ...
```

---

## Language-Specific Implementations

### Python / Django

> See [Django Guide](../django.md#n1-query-optimization)

```python
# ForeignKey / OneToOne → select_related (SQL JOIN)
books = Book.objects.select_related("publisher")

# M2M / reverse FK → prefetch_related (2 queries + Python merge)
authors = Author.objects.prefetch_related("books")

# Nested prefetch
authors = Author.objects.prefetch_related("books__publisher")

# Fine-grained control with Prefetch objects
from django.db.models import Prefetch
authors = Author.objects.prefetch_related(
    Prefetch("books", queryset=Book.objects.filter(published=True), to_attr="published_books")
)
```

### Python / SQLAlchemy (FastAPI)

> See [FastAPI Guide](../fastapi.md#database-sessions--n1)

```python
from sqlalchemy.orm import selectinload

# selectinload: batch load via IN clause (recommended for async)
stmt = select(Order).options(selectinload(Order.customer))

# joinedload: JOIN load
stmt = select(Order).options(joinedload(Order.customer))
```

### Java / JPA (Spring Boot)

> See [Java Guide](../java.md)

```java
// ❌ FetchType.EAGER or lazy loading triggered in a loop
@OneToMany(fetch = FetchType.EAGER)  // dangerous!

// ✅ JOIN FETCH
@Query("SELECT u FROM User u JOIN FETCH u.orders")
List<User> findAllWithOrders();

// ✅ @EntityGraph (declarative)
@EntityGraph(attributePaths = {"orders", "profile"})
List<User> findAll();

// ✅ @BatchSize (reduces N+1 to N/batchSize + 1)
@OneToMany
@BatchSize(size = 50)
private List<Order> orders;
```

### C# / EF Core

> See [C# Guide](../csharp.md)

```csharp
// ❌ N+1: foreach triggers lazy loading
foreach (var blog in await context.Blogs.ToListAsync())
    foreach (var post in blog.Posts)  // one query per iteration!

// ✅ Include + ThenInclude
var blogs = await context.Blogs
    .Include(b => b.Posts)
    .ToListAsync();

// ✅ Projection (safest, avoids over-fetching)
var data = await context.Blogs
    .Select(b => new { b.Url, PostTitles = b.Posts.Select(p => p.Title) })
    .ToListAsync();
```

### PHP / Laravel / Doctrine

> See [PHP Guide](../php.md)

```php
// ❌ Query inside loop
foreach ($orders as $order) {
    $customer = $customerRepo->find($order->customerId);
    render($order, $customer);
}

// ✅ Batch prefetch
$customerIds = array_unique(array_map(fn($o) => $o->customerId, $orders));
$customers = $customerRepo->findByIds($customerIds);

foreach ($orders as $order) {
    render($order, $customers[$order->customerId] ?? null);
}

// Laravel Eloquent: with()
$orders = Order::with('customer')->get();

// Doctrine: JOIN FETCH
$dql = 'SELECT o, c FROM Order o JOIN o.customer c';
```

### TypeScript / Prisma

```typescript
// ❌ N+1
const users = await prisma.user.findMany();
for (const user of users) {
    user.posts = await prisma.post.findMany({ where: { userId: user.id } });
}

// ✅ include (Prisma auto-generates JOIN or batch query)
const users = await prisma.user.findMany({
    include: { posts: true },
});

// ✅ Nested include
const users = await prisma.user.findMany({
    include: {
        posts: {
            include: { comments: true },
        },
    },
});
```

---

## Review Checklist

### Detection
- [ ] SQL logging or query-count monitoring is enabled
- [ ] Tests assert query counts
- [ ] APM tools are configured with N+1 alerts

### Fixes
- [ ] ForeignKey / OneToOne relationships use JOIN eager loading
- [ ] M2M / reverse relationships use IN batch prefetch
- [ ] Avoid triggering database queries inside loops
- [ ] Use projection to fetch only required fields

### Architecture
- [ ] List APIs are paginated to avoid loading too many records at once
- [ ] GraphQL scenarios use DataLoader
- [ ] Caching strategy (Redis) for frequently read related data
