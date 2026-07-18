# Architecture Review Guide

A guide for architecture design reviews, helping you assess whether the code's architecture is sound and the design is appropriate.

## SOLID Principles Checklist

### S - Single Responsibility Principle (SRP)

**What to check:**
- Does this class/module have only one reason to change?
- Do all methods in the class serve the same purpose?
- If you had to describe this class to a non-technical person, could you do it in one sentence?

**Signals to spot during code review:**
```
⚠️ Class names contain generic words like "And", "Manager", "Handler", "Processor"
⚠️ A class exceeds 200–300 lines of code
⚠️ A class has more than 5–7 public methods
⚠️ Different methods operate on completely different data
```

**Review questions:**
- "What responsibilities does this class have? Can it be split?"
- "If requirement X changes, which methods need to change? What about requirement Y?"

### O - Open/Closed Principle (OCP)

**What to check:**
- When adding new functionality, do you need to modify existing code?
- Can new behavior be added through extension (inheritance, composition)?
- Are there large if/else or switch statements handling different types?

**Signals to spot during code review:**
```
⚠️ switch/if-else chains handling different types
⚠️ Adding new functionality requires modifying core classes
⚠️ Type checks (instanceof, typeof) scattered throughout the code
```

**Review questions:**
- "If we add a new X type, which files need to change?"
- "Will this switch statement keep growing as new types are added?"

### L - Liskov Substitution Principle (LSP)

**What to check:**
- Can subclasses fully replace the parent class wherever it is used?
- Do subclasses change the expected behavior of parent methods?
- Do subclasses throw exceptions not declared by the parent?

**Signals to spot during code review:**
```
⚠️ Explicit type casting
⚠️ Subclass methods throw NotImplementedException
⚠️ Subclass methods are empty implementations or only return
⚠️ Code using the base class needs to check the concrete type
```

**Review questions:**
- "If we substitute a subclass for the parent class, does calling code need to change?"
- "Does this method's behavior in the subclass honor the parent's contract?"

### I - Interface Segregation Principle (ISP)

**What to check:**
- Is the interface small and focused enough?
- Are implementers forced to implement methods they do not need?
- Do clients depend on methods they do not use?

**Signals to spot during code review:**
```
⚠️ Interface has more than 5–7 methods
⚠️ Implementing classes have empty methods or throw NotImplementedException
⚠️ Interface name is too broad (IManager, IService)
⚠️ Different clients use only a subset of the interface's methods
```

**Review questions:**
- "Are all methods in this interface used by every implementing class?"
- "Can this large interface be split into smaller, purpose-specific interfaces?"

### D - Dependency Inversion Principle (DIP)

**What to check:**
- Do high-level modules depend on abstractions rather than concrete implementations?
- Is dependency injection used instead of directly `new`-ing objects?
- Are abstractions defined by high-level modules rather than low-level modules?

**Signals to spot during code review:**
```
⚠️ High-level modules directly new concrete classes from low-level modules
⚠️ Imports concrete implementations instead of interfaces/abstract classes
⚠️ Configuration and connection strings hardcoded in business logic
⚠️ Difficult to write unit tests for a class
```

**Review questions:**
- "Can this class's dependencies be replaced with mocks in tests?"
- "If we swap the database/API implementation, how many places need to change?"

---

## Architecture Anti-Pattern Identification

### Critical Anti-Patterns

| Anti-pattern | Signals | Impact |
|--------|----------|------|
| **Big Ball of Mud** | No clear module boundaries; any code can call any other code | Hard to understand, modify, and test |
| **God Object** | A single class takes on too many responsibilities; knows too much, does too much | High coupling; hard to reuse and test |
| **Spaghetti Code** | Chaotic control flow, goto or deep nesting; hard to trace execution paths | Hard to understand and maintain |
| **Lava Flow** | Ancient code nobody dares touch; lacks documentation and tests | Technical debt accumulates |

### Design Anti-Patterns

| Anti-pattern | Signals | Recommendation |
|--------|----------|------|
| **Golden Hammer** | Using the same technology/pattern for every problem | Choose the right solution for each problem |
| **Gas Factory (Over-engineering)** | Solving simple problems with complex solutions; design patterns abused | YAGNI—start simple, add complexity only when needed |
| **Boat Anchor** | Unused code written for "we might need it later" | Delete unused code; write it when needed |
| **Copy-Paste Programming** | The same logic appears in multiple places | Extract shared methods or modules |

### Review Questions

```markdown
🔴 [blocking] "This class has 2000 lines of code; consider splitting it into several focused classes"
🟡 [important] "This logic is duplicated in 3 places—consider extracting a shared method?"
💡 [suggestion] "This switch statement could be replaced with the Strategy pattern for easier extension"
```

---

## Coupling and Cohesion Assessment

### Coupling Types (best to worst)

| Type | Description | Example |
|------|------|------|
| **Message coupling** ✅ | Data passed via parameters | `calculate(price, quantity)` |
| **Data coupling** ✅ | Share simple data structures | `processOrder(orderDTO)` |
| **Stamp coupling** ⚠️ | Share complex data structures but use only part | Pass entire User object but only use name |
| **Control coupling** ⚠️ | Control flags passed in affect behavior | `process(data, isAdmin=true)` |
| **Common coupling** ❌ | Share global variables | Multiple modules read/write the same global state |
| **Content coupling** ❌ | Direct access to another module's internals | Directly manipulate another class's private fields |

### Cohesion Types (best to worst)

| Type | Description | Quality |
|------|------|------|
| **Functional cohesion** | All elements complete a single task | ✅ Best |
| **Sequential cohesion** | Output of one step is input to the next | ✅ Good |
| **Communicational cohesion** | Operate on the same data | ⚠️ Acceptable |
| **Temporal cohesion** | Tasks executed at the same time | ⚠️ Poor |
| **Logical cohesion** | Logically related but functionally different | ❌ Bad |
| **Coincidental cohesion** | No obvious relationship | ❌ Worst |

### Metric Reference

```yaml
Coupling metrics:
  CBO (Coupling Between Objects):
    good: < 5
    warning: 5-10
    danger: > 10

  Ce (Efferent coupling):
    description: How many external classes this depends on
    good: < 7

  Ca (Afferent coupling):
    description: How many classes depend on this
    high value means: Large blast radius on change; needs stability

Cohesion metrics:
  LCOM4 (Lack of Cohesion of Methods):
    1: Single responsibility ✅
    2-3: May need splitting ⚠️
    >3: Should split ❌
```

### Review Questions

- "How many other modules does this module depend on? Can that be reduced?"
- "How many other places are affected if we change this class?"
- "Do all methods in this class operate on the same data?"

---

## Layered Architecture Review

### Clean Architecture Layer Check

```
┌─────────────────────────────────────┐
│         Frameworks & Drivers        │ ← Outermost: Web, DB, UI
├─────────────────────────────────────┤
│         Interface Adapters          │ ← Controllers, Gateways, Presenters
├─────────────────────────────────────┤
│          Application Layer          │ ← Use Cases, Application Services
├─────────────────────────────────────┤
│            Domain Layer             │ ← Entities, Domain Services
└─────────────────────────────────────┘
          ↑ Dependencies point inward only ↑
```

### Dependency Rule Check

**Core rule: source-code dependencies may only point inward**

```typescript
// ❌ Violates dependency rule: Domain layer depends on Infrastructure
// domain/User.ts
import { MySQLConnection } from '../infrastructure/database';

// ✅ Correct: Domain defines the interface; Infrastructure implements
// domain/UserRepository.ts (interface)
interface UserRepository {
  findById(id: string): Promise<User>;
}

// infrastructure/MySQLUserRepository.ts (implementation)
class MySQLUserRepository implements UserRepository {
  findById(id: string): Promise<User> { /* ... */ }
}
```

### Review Checklist

**Layer boundary check:**
- [ ] Does the Domain layer have external dependencies (database, HTTP, filesystem)?
- [ ] Does the Application layer directly access the database or call external APIs?
- [ ] Does the Controller contain business logic?
- [ ] Are there cross-layer calls (e.g., UI calling Repository directly)?

**Separation of concerns check:**
- [ ] Is business logic separated from presentation logic?
- [ ] Is data access encapsulated in a dedicated layer?
- [ ] Is configuration and environment-specific code centralized?

### Review Questions

```markdown
🔴 [blocking] "Domain entity directly imports a database connection, violating the dependency rule"
🟡 [important] "Controller contains business calculation logic; move it to the Service layer"
💡 [suggestion] "Consider using dependency injection to decouple these components"
```

---

## Design Pattern Usage Assessment

### When to Use Design Patterns

| Pattern | Good fit | Poor fit |
|------|----------|------------|
| **Factory** | Need to create different object types; type determined at runtime | Only one type, or type is fixed |
| **Strategy** | Algorithm must switch at runtime; multiple interchangeable behaviors | Only one algorithm, or algorithm never changes |
| **Observer** | One-to-many dependency; state changes must notify multiple objects | Simple direct calls are enough |
| **Singleton** | Truly need a globally unique instance (e.g., config manager) | Object can be passed via dependency injection |
| **Decorator** | Need to add responsibilities dynamically; avoid inheritance explosion | Fixed responsibilities; no dynamic composition needed |

### Over-Design Warning Signals

```
⚠️ Patternitis signals:

1. Simple if/else replaced by Strategy + Factory + Registry
2. Interface with only one implementation
3. Abstraction layers added for "we might need it later"
4. Line count grows sharply because of pattern application
5. Newcomers take a long time to understand the structure
```

### Review Principles

```markdown
✅ Correct pattern use:
- Solves a real extensibility problem
- Code is easier to understand and test
- Adding new features becomes simpler

❌ Overuse of patterns:
- Using patterns for their own sake
- Adds unnecessary complexity
- Violates YAGNI
```

### Review Questions

- "What specific problem does this pattern solve?"
- "What would go wrong if we did not use this pattern?"
- "Is the value of this abstraction worth its complexity?"

---

## Extensibility Assessment

### Extensibility Checklist

**Functional extensibility:**
- [ ] Does adding new features require changing core code?
- [ ] Are extension points provided (hooks, plugins, events)?
- [ ] Is configuration externalized (config files, environment variables)?

**Data extensibility:**
- [ ] Does the data model support new fields?
- [ ] Have data-growth scenarios been considered?
- [ ] Do queries have appropriate indexes?

**Load extensibility:**
- [ ] Can the system scale horizontally (add more instances)?
- [ ] Is there state affinity (session, local cache)?
- [ ] Does the database use connection pooling?

### Extension Point Design Check

```typescript
// ✅ Good extension design: events/hooks
class OrderService {
  private hooks: OrderHooks;

  async createOrder(order: Order) {
    await this.hooks.beforeCreate?.(order);
    const result = await this.save(order);
    await this.hooks.afterCreate?.(result);
    return result;
  }
}

// ❌ Poor extension design: all behavior hardcoded
class OrderService {
  async createOrder(order: Order) {
    await this.sendEmail(order);        // hardcoded
    await this.updateInventory(order);  // hardcoded
    await this.notifyWarehouse(order);  // hardcoded
    return await this.save(order);
  }
}
```

### Review Questions

```markdown
💡 [suggestion] "If we need to support new payment methods later, is this design easy to extend?"
🟡 [important] "Logic here is hardcoded—consider configuration or the Strategy pattern?"
📚 [learning] "Event-driven architecture could make this feature easier to extend"
```

---

## Code Structure Best Practices

### Directory Organization

**Organize by feature/domain (recommended):**
```
src/
├── user/
│   ├── User.ts           (entity)
│   ├── UserService.ts    (service)
│   ├── UserRepository.ts (data access)
│   └── UserController.ts (API)
├── order/
│   ├── Order.ts
│   ├── OrderService.ts
│   └── ...
└── shared/
    ├── utils/
    └── types/
```

**Organize by technical layer (not recommended):**
```
src/
├── controllers/     ← different domains mixed together
│   ├── UserController.ts
│   └── OrderController.ts
├── services/
├── repositories/
└── models/
```

### Naming Convention Check

| Type | Convention | Example |
|------|------|------|
| Class names | PascalCase, nouns | `UserService`, `OrderRepository` |
| Method names | camelCase, verbs | `createUser`, `findOrderById` |
| Interface names | `I` prefix or no prefix | `IUserService` or `UserService` |
| Constants | UPPER_SNAKE_CASE | `MAX_RETRY_COUNT` |
| Private fields | Leading underscore or none | `_cache` or `#cache` |

### File Size Guidelines

```yaml
Suggested limits:
  single file: < 300 lines
  single function: < 50 lines
  single class: < 200 lines
  function parameters: < 4
  nesting depth: < 4 levels

When limits are exceeded:
  - Consider splitting into smaller units
  - Prefer composition over inheritance
  - Extract helper functions or classes
```

### Review Questions

```markdown
🟢 [nit] "This 500-line file could be split by responsibility"
🟡 [important] "Organize directories by feature domain rather than technical layer"
💡 [suggestion] "Function name `process` is vague—consider `calculateOrderTotal`?"
```

---

## Quick Reference Checklist

### 5-Minute Architecture Review

```markdown
□ Is dependency direction correct? (outer layers depend on inner)
□ Are there circular dependencies?
□ Is core business logic decoupled from framework/UI/database?
□ Are SOLID principles followed?
□ Are obvious anti-patterns present?
```

### Red Flags (must address)

```markdown
🔴 God Object — single class over 1000 lines
🔴 Circular dependency — A → B → C → A
🔴 Domain layer contains framework dependencies
🔴 Hardcoded configuration and secrets
🔴 External service calls with no interface
```

### Yellow Flags (should address)

```markdown
🟡 Coupling between objects (CBO) > 10
🟡 More than 5 method parameters
🟡 Nesting depth greater than 4
🟡 Duplicated code block > 10 lines
🟡 Interface with only one implementation
```

---

## Recommended Tools

| Tool | Purpose | Language Support |
|------|------|----------|
| **SonarQube** | Code quality, coupling analysis | Multi-language |
| **NDepend** | Dependency analysis, architecture rules | .NET |
| **JDepend** | Package dependency analysis | Java |
| **Madge** | Module dependency graph | JavaScript/TypeScript |
| **ESLint** | Code style, complexity checks | JavaScript/TypeScript |
| **CodeScene** | Technical debt, hotspot analysis | Multi-language |

---

## References

- [Clean Architecture - Uncle Bob](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
- [SOLID Principles in Code Review - JetBrains](https://blog.jetbrains.com/upsource/2015/08/31/what-to-look-for-in-a-code-review-solid-principles-2/)
- [Software Architecture Anti-Patterns](https://medium.com/@christophnissle/anti-patterns-in-software-architecture-3c8970c9c4f5)
- [Coupling and Cohesion in System Design](https://www.geeksforgeeks.org/system-design/coupling-and-cohesion-in-system-design/)
- [Design Patterns - Refactoring Guru](https://refactoring.guru/design-patterns)
