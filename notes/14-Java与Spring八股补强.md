# Java 与 Spring 八股补强

> 这份笔记只收录**面试真实被追问过的**高频八股。
> 每个知识点都尽量关联到你的两个项目代码，拒绝纯背诵。
> 原则：用比喻讲原理，用故事记结论。

---

## 一、JVM 类加载

### Q1: Java 类加载过程？

**考察**：JVM 基础（你上次说不了解，这次必须能说！）

**A**：一个 `.class` 文件从磁盘到内存，要经过五步——可以理解为"新员工入职流程"：

```
加载 → 验证 → 准备 → 解析 → 初始化
 ↑       ↑       ↑       ↑       ↑
HR找简历  查背景   发工牌   带到工位  正式干活
```

| 阶段 | 干了啥 | 比喻 |
|------|--------|------|
| **加载 Loading** | 通过类全名找到 `.class` 文件，读字节码到内存，创建 `Class` 对象 | HR 从人才库里找到你的简历 |
| **验证 Verification** | 检查字节码格式是否合法（魔数 `CAFEBABE`、版本号等） | 查你学历有没有造假 |
| **准备 Preparation** | 给**类变量**（`static`）分配内存并赋默认值（0 / null / false） | 发工牌、分配工位，但还没开始干活 |
| **解析 Resolution** | 把符号引用替换为直接引用（比如方法名 → 内存地址） | 把"去找张三"换成"去 3 楼 302 工位" |
| **初始化 Initialization** | 执行 `<clinit>()` 方法——静态变量赋值 + 静态代码块 | 正式开始干活！ |

**追问：`static int x = 10` 在哪两个阶段被赋值？**
- **准备阶段**：`x = 0`（默认值）
- **初始化阶段**：`x = 10`（你代码里写的值）

---

### Q2: 双亲委派模型是什么？为什么需要？

**A**：类加载器有层级关系，加载一个类时**先问爸爸能不能加载，爸爸不行再自己动手**：

```
    Bootstrap ClassLoader（爷爷）  ← 加载 rt.jar（String、Object 等核心类）
           ↑ 委派
    Extension ClassLoader（爸爸）  ← 加载 ext 目录
           ↑ 委派
    Application ClassLoader（儿子）← 加载你写的代码（classpath）
           ↑ 委派
    自定义 ClassLoader（孙子）      ← 热加载、隔离等特殊需求
```

**为什么需要？** 安全！防止有人写一个 `java.lang.String` 来冒充 JDK 的 String。有了双亲委派，不管谁加载 String，最终都是 Bootstrap 加载的那个官方版本。

**追问：怎么打破双亲委派？**
- **SPI 机制**（比如 JDBC）——核心类 `DriverManager` 在 Bootstrap 加载，但它要加载你 classpath 下的数据库驱动。解决方案：**线程上下文类加载器** `Thread.getContextClassLoader()`
- **Tomcat**——每个 Web 应用有自己的 `WebAppClassLoader`，**先自己加载再委派给父级**（反着来），实现应用隔离
- **Spring Boot Fat JAR**——嵌套 JAR 里的类，用自定义的 `LaunchedURLClassLoader` 加载

---

### Q3: `Class.forName()` 和 `ClassLoader.loadClass()` 的区别？

**A**：
- `Class.forName("xxx")` → 加载 + 初始化（执行 `static` 块）
- `ClassLoader.loadClass("xxx")` → 只加载，不初始化

典型例子：JDBC 的 `Class.forName("com.mysql.cj.jdbc.Driver")` 必须用 `forName`，因为 Driver 类的 `static` 块里注册了驱动。用 `loadClass` 的话驱动不会被注册。

---

## 二、Spring 核心

### Q4: Spring Bean 的生命周期？

**考察**：Spring 基础中的基础

**A**：一个 Bean 从出生到死亡，走过这些阶段——

```
实例化(new) → 属性注入(@Autowired) → Aware接口回调
  → @PostConstruct → InitializingBean → init-method
  → 使用中...
  → @PreDestroy → DisposableBean → destroy-method
```

简化记忆版（面试说这个就够了）：
1. **创建对象**——调构造器 `new`
2. **注入依赖**——`@Autowired` 填充属性
3. **初始化**——`@PostConstruct` → `afterPropertiesSet()` → 自定义 `init`
4. **使用**
5. **销毁**——`@PreDestroy` → `destroy()` → 自定义 `destroy`

**关联你的项目**：`RedisCacheHandlerUtil` 实现的 `CommandLineRunner` 是在所有 Bean 初始化完毕**之后**才执行的，比 `@PostConstruct` 更晚。

---

### Q5: AOP 底层原理？JDK 动态代理 vs CGLIB？

**A**：

| | JDK 动态代理 | CGLIB |
|---|---|---|
| 原理 | 基于**接口**，生成接口的代理实现类 | 基于**继承**，生成目标类的子类 |
| 要求 | 目标类必须实现接口 | 目标类不能是 `final` |
| 性能 | 略慢（反射调用） | 略快（直接调用方法） |
| Spring 默认 | Spring Boot 2.0+ 默认用 **CGLIB** | ← |

**打个比方**：JDK 代理像演替身——替身和演员长得像但不是同一个人（实现同一个接口）；CGLIB 像克隆——克隆体就是目标的子类（继承）。

**关联你的项目**：你秒杀 Consumer 里的 `@Transactional` 同类调用失效，就是因为同一个类内部的调用**绕过了 CGLIB 代理**。

---

### Q6: `@Transactional` 事务失效的场景有哪些？

**A**：这是高频追问，至少能说出 4 种：

| # | 失效场景 | 原因 | 解决 |
|---|---------|------|------|
| 1 | **同类调用**（你踩过的坑！） | 内部方法调用走 `this`，不走代理 | 抽到独立 Service |
| 2 | **方法不是 public** | Spring AOP 只拦截 `public` 方法 | 改成 `public` |
| 3 | **异常被 catch 吞了** | Spring 看不到异常，不回滚 | 不要吞异常，或手动 `setRollbackOnly()` |
| 4 | **抛的是 checked 异常** | 默认只对 `RuntimeException` 回滚 | 加 `@Transactional(rollbackFor = Exception.class)` |
| 5 | **数据库引擎不支持** | MyISAM 不支持事务 | 用 InnoDB |
| 6 | **传播行为错误** | `REQUIRES_NEW` 单独事务不受外层影响 | 理解传播行为再选 |

---

## 三、ThreadLocal

### Q7: ThreadLocal 原理？

**考察**：并发编程基础

**A**：ThreadLocal 的本质是——**每个线程自己有一个私人小仓库**，往里面存东西别的线程看不到。

打个比方：办公室里每个人桌上有个抽屉（ThreadLocal），你往自己抽屉里放了一把钥匙，别人打开自己的抽屉是看不到你的钥匙的。

底层实现：<br>
每个 `Thread` 对象里有一个 `ThreadLocalMap`（就是那个抽屉），key 是 ThreadLocal 对象本身，value 是你存的值。

```
Thread-1:  ThreadLocalMap { threadLocal1 → "用户A", threadLocal2 → "token-xxx" }
Thread-2:  ThreadLocalMap { threadLocal1 → "用户B", threadLocal2 → "token-yyy" }
```

### Q8: ThreadLocal 内存泄漏问题？

**A**：这是面试**必追问**的点。

ThreadLocalMap 的 Entry 继承了 `WeakReference<ThreadLocal>`：

```
Entry {
    key = WeakReference<ThreadLocal>  ← 弱引用！GC 时会被回收
    value = Object                    ← 强引用！GC 不会回收
}
```

问题链路：
1. 你用完 ThreadLocal，没有手动 `remove()`
2. ThreadLocal 对象没有其他强引用了 → GC 回收了 ThreadLocal → **key 变成 null**
3. 但 **value 还在**！因为 Entry → value 是强引用
4. key = null 的 Entry 永远无法被访问，但也不会被回收 → **内存泄漏**

**解决方案**：用完之后**一定要调 `threadLocal.remove()`**。就像用完厕所要冲水一样——不冲的话，下一个人（线程池复用线程时）会看到你留下的东西。

### Q9: ThreadLocal 在你项目里的关联？

**A**：我的项目里没有直接用 ThreadLocal，但 `@TokenToMallUser` 注解的参数解析器（`TokenToMallUserMethodArgumentResolver`）里做的事情——从 Header 取 token → 查数据库 → 拿到用户对象 → 注入到 Controller 方法参数——这套逻辑如果用 ThreadLocal 来做会是这样：

```java
// 方案 A（我的做法）：自定义注解 + 参数解析器
public Result execute(@TokenToMallUser MallUser user) { ... }

// 方案 B（ThreadLocal 做法）：拦截器存，Controller 取
public class UserHolder {
    private static final ThreadLocal<MallUser> userThread = new ThreadLocal<>();
    public static void set(MallUser user) { userThread.set(user); }
    public static MallUser get() { return userThread.get(); }
    public static void remove() { userThread.remove(); }  // 一定要有！
}
```

方案 A 更 Spring 风格，方案 B 更通用（在非 Controller 层也能拿到用户信息）。

**追问：线程池中 ThreadLocal 会有什么问题？**

线程池的线程是复用的！前一个请求在线程里存了用户 A 的信息，如果没 `remove()`，下一个请求复用这个线程时会拿到用户 A 的信息——**串数据了**！

解决方案：
- `InheritableThreadLocal`——可以从父线程传给子线程，但线程池复用时也有问题
- 阿里的 `TransmittableThreadLocal (TTL)`——解决线程池场景下的传递问题

---

## 四、数据库索引

### Q10: 为什么 MySQL 用 B+ 树做索引？

**考察**：数据结构选型

**A**：先说结论，再说排除法——B+ 树是**磁盘读写最优**的选择。

| 数据结构 | 为什么不选 |
|---------|----------|
| **Hash** | 等值查询 O(1) 很快，但**不支持范围查询**（`WHERE price > 100`） |
| **红黑树** | 树太高！百万数据树高约 20 层 = 20 次磁盘 IO，太慢 |
| **B 树** | 每个节点既存 key 又存 data，一个节点能放的 key 少，树更高 |
| **B+ 树** ✅ | 非叶子节点只存 key（不存 data），一个节点能放更多 key → 树更矮（3~4 层就能存几千万数据 = 3~4 次磁盘 IO） |

B+ 树的两个杀手特性：
1. **叶子节点用链表串起来** → 范围查询直接顺着链表遍历，不用回到树上
2. **所有数据都在叶子节点** → 查询路径长度一致，性能稳定

---

### Q11: 聚簇索引和非聚簇索引的区别？

**A**：

| | 聚簇索引（主键索引） | 非聚簇索引（二级索引） |
|---|---|---|
| 叶子节点存的是 | **整行数据** | **主键值**（不是整行数据！） |
| 一张表有几个 | 只有 1 个（主键决定数据物理存储顺序） | 可以有多个 |
| 查询流程 | 直接拿到整行 | 先拿到主键 → 再回主键索引查整行（**回表**） |

**回表**：用二级索引查到主键，再拿主键去聚簇索引查整行数据——走了两次索引树。

**覆盖索引**：如果你查的字段**全部被索引覆盖**了（比如 `SELECT id, name FROM user WHERE name = '张三'`，而 name 上有索引，叶子节点里就有 id），不需要回表——这叫覆盖索引，`EXPLAIN` 里显示 `Using index`。

---

### Q12: 索引在什么情况下会失效？

**考察**：重点！面试高频！

**A**：能答出 6 种以上就很稳了——

| # | 失效场景 | 例子 | 为什么失效 |
|---|---------|------|----------|
| 1 | **对索引列做函数/计算** | `WHERE YEAR(create_time) = 2024` | 函数破坏了 B+ 树的有序性，没法用索引 |
| 2 | **隐式类型转换** | 字段是 varchar，查询 `WHERE phone = 13800138000`（没加引号） | MySQL 会对字段做函数转换 → 同上 |
| 3 | **最左前缀原则违反** | 联合索引 `(a, b, c)`，查询 `WHERE b = 1 AND c = 2`（跳过了 a） | 联合索引的 B+ 树先按 a 排序，a 不确定就没法二分查找 |
| 4 | **`LIKE` 左模糊** | `WHERE name LIKE '%张'` | B+ 树按字符串从左到右排序，左边不确定就没法定位 |
| 5 | **`OR` 条件** | `WHERE a = 1 OR b = 2`（a 有索引，b 没有） | 有一个没索引就得全表扫描 |
| 6 | **`!=` / `NOT IN`** | `WHERE status != 1` | 否定条件扫描范围太大，优化器可能觉得全表扫更快 |
| 7 | **`IS NULL` / `IS NOT NULL`** | 取决于 NULL 值比例 | NULL 多时优化器倾向全表扫 |
| 8 | **数据量太小** | 表就 100 条数据 | 优化器判断全表扫比走索引更快（少一次 IO） |

**打个比方**：索引就像字典的拼音目录。正常查"张"，翻到 Z 就行了。但如果你要查"名字里有'张'的所有字"（左模糊），目录没用了，只能一页页翻全本字典。

### Q13: 你项目里的 SQL 走了什么索引？

**A**：秒杀扣库存的 SQL：

```sql
UPDATE tb_newbee_mall_seckill
SET seckill_num = seckill_num - 1
WHERE seckill_id = ? AND seckill_num > 0
```

- `seckill_id` 是主键 → 走**聚簇索引**，直接定位到那一行
- `seckill_num > 0` 是范围条件，但因为已经**通过主键精确定位**了，这个条件只是在单行上做过滤，不影响索引选择
- 执行计划：`type = const`（主键精确匹配，最快）

---

## 五、并发基础

### Q14: `synchronized` 的原理？锁升级过程？

**考察**：JVM 锁机制

**A**：`synchronized` 底层靠对象头里的 **Mark Word** 实现，会经历锁升级——

```
无锁 → 偏向锁 → 轻量级锁 → 重量级锁
        ↑          ↑           ↑
      只有一个线程  有竞争但不激烈  竞争激烈
```

| 锁状态 | 场景 | 原理 | 性能 |
|--------|------|------|------|
| **偏向锁** | 只有一个线程访问 | Mark Word 记录线程 ID，下次同一线程来直接放行 | 几乎无开销 |
| **轻量级锁** | 两个线程交替访问（不同时竞争） | CAS 修改 Mark Word，失败的线程**自旋等待** | 自旋消耗 CPU 但不阻塞 |
| **重量级锁** | 多个线程同时竞争 | 升级为操作系统的 Mutex Lock，失败的线程**阻塞挂起** | 性能最差，有上下文切换 |

**关联你的项目**：你的雪花算法 `SnowflakeIdGenerator.nextId()` 用了 `synchronized`。在单 Consumer 场景下基本就是偏向锁，几乎零开销。如果用多 Consumer 并发消费，会升级到轻量级锁。

---

### Q15: volatile 关键字的作用？

**A**：两个作用，记住就行：

1. **保证可见性**——一个线程修改了 volatile 变量，其他线程**立刻**看到新值（而不是看到 CPU 缓存里的旧值）
2. **禁止指令重排序**——编译器和 CPU 不会把 volatile 变量相关的指令顺序打乱

**不保证原子性**！`volatile int count; count++;` 在多线程下还是会出问题——因为 `count++` 是三步操作（读-改-写），volatile 只保证"读到最新值"，不保证"读-改-写"是原子的。

**经典用途**：单例模式的双重检查锁（DCL）中的 `private static volatile Singleton instance;`

---

### Q16: CAS 是什么？ABA 问题？

**A**：CAS = Compare And Swap（比较再交换），是乐观锁的底层实现。

```
CAS(内存地址, 期望值, 新值)
  如果 内存值 == 期望值:
      内存值 = 新值, 返回 true
  否则:
      什么也不做, 返回 false
```

整个操作是 **CPU 指令级别的原子操作**（`cmpxchg` 指令），不需要加锁。

Java 里 `AtomicInteger.incrementAndGet()` 底层就是 CAS 自旋：
```java
do {
    oldValue = get();           // 读当前值
    newValue = oldValue + 1;    // 算新值
} while (!compareAndSet(oldValue, newValue));  // CAS 更新，失败了再来
```

**ABA 问题**：前面 Q7 已经讲过——值从 A→B→A，CAS 以为没变过。解决方案：`AtomicStampedReference`，加一个版本号戳。

---

### Q17: 线程池核心参数？拒绝策略？

**A**：

```java
new ThreadPoolExecutor(
    corePoolSize,     // 核心线程数（平时保持的线程数）
    maximumPoolSize,  // 最大线程数（忙不过来时临时扩招）
    keepAliveTime,    // 临时工的空闲存活时间（没活干多久后裁掉）
    unit,             // 时间单位
    workQueue,        // 阻塞队列（任务排队的地方）
    threadFactory,    // 线程工厂（给线程起名字等）
    handler           // 拒绝策略（队列满了 + 线程满了怎么办）
);
```

处理流程（面试必画的图）：
```
新任务来了
  → 核心线程有空吗？→ 有 → 直接执行
  → 没空 → 队列满了吗？→ 没满 → 放队列排队
  → 满了 → 还能创建临时线程吗？→ 能 → 创建临时线程执行
  → 不能 → 触发拒绝策略！
```

四种拒绝策略：

| 策略 | 行为 | 比喻 |
|------|------|------|
| **AbortPolicy**（默认） | 直接抛 `RejectedExecutionException` | 门口贴"满员了，走开" |
| **CallerRunsPolicy** | 让提交任务的线程自己执行 | "你自己去干吧"（你 Agent 项目里的背压用了这个！） |
| **DiscardPolicy** | 默默丢弃，不报错 | 装没看见 |
| **DiscardOldestPolicy** | 丢掉队列里最老的任务，放新的进去 | 后来的把前面排队的挤掉 |

**关联你的 Agent 项目**：语义分块模块的背压防 OOM（11-语义分块与重排版架构.md）里用的就是 `CallerRunsPolicy`——队列满了让主线程自己去干推理，达到物理层面的自动限流。
