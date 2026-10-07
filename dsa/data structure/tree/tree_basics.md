# Trees: What's Really Happening

## 1. The core idea

A tree is not a special memory structure. It is **ordinary structs that store the addresses of other structs**. An "edge" is just an 8-byte integer holding a memory address (on 64-bit machines). `nil`/`None` is address `0`.

```text
offset   0        8        16       24
         +--------+--------+--------+
         |  val   |  left  | right  |
         +--------+--------+--------+
          8 bytes  8 bytes  8 bytes
```

Each node is 24 bytes in both Go and Rust. A tree is many of these, scattered across the heap and linked by addresses.

## 2. Mental model: tree as heap memory

```text
Tree:        10
            /  \
           5    15

Heap (made-up addresses):

0x1000: | 10 | 0x2000 | 0x3000 |
0x2000: |  5 |   0x0  |   0x0  |
0x3000: | 15 |   0x0  |   0x0  |

root = 0x1000   <- the ONLY entry point
```

**Access rule:** you hold only `root`. To reach any node, you must follow addresses from the root. There is no `tree[i]`. That is why search is a walk, and why cost depends on **height**, not size.

## 3. Dry run: search for 7

Tree: `10 → (5 → (3, 7)), (15 → (12, 18))`

| Step | cur (addr) | Compare | Action |
|---|---|---|---|
| 1 | 0x1000 (10) | 7 < 10 | cur = cur.left |
| 2 | 0x2000 (5) | 7 > 5 | cur = cur.right |
| 3 | 0x4000 (7) | 7 == 7 | found |

Each step is a **dependent load**: the CPU can't fetch the next node until the current node's `left`/`right` field arrives from memory. This is *pointer chasing*.

## 4. Why trees are slower than their Big-O suggests

```text
Array: contiguous, prefetcher loves it
[ a ][ b ][ c ][ d ]   -> 1 cache line, 4 elements

Tree: each node a separate allocation
 0x1000        0x7F20        0x3A80
 [node]  ...   [node]  ...   [node]
 cache miss    cache miss    cache miss
```

A cache miss costs ~100 ns versus ~1 ns for a hit. A balanced tree with 1M nodes takes ~20 hops, which can mean up to 20 misses. This is why real systems use B-trees (many keys per node) or array-backed trees.

## 5. Recursion under the hood: the call stack

`InOrder` doesn't hold a "position" in the tree. The **call stack** does.

```text
InOrder(10)
  InOrder(5)
    InOrder(nil)  <- returns
    visit(5)
    InOrder(nil)

Stack at deepest point:
+----------------+
| InOrder(nil)   | <- top
+----------------+
| InOrder(5)     |
+----------------+
| InOrder(10)    |
+----------------+
```

Stack depth equals tree height. A degenerate tree (sorted inserts) has height `n`, which can overflow the stack. Go's goroutine stacks grow dynamically (up to 1 GB by default). Rust's main thread has about 8 MB, so it overflows much sooner.

## 6. Go implementation

```go
package main

import "fmt"

type Node struct {
	Val         int
	Left, Right *Node
}

// Returns the (possibly new) subtree root.
func Insert(root *Node, v int) *Node {
	if root == nil {
		return &Node{Val: v} // escapes to heap
	}
	if v < root.Val {
		root.Left = Insert(root.Left, v)
	} else if v > root.Val {
		root.Right = Insert(root.Right, v)
	}
	return root
}

// Iterative: no stack growth, just one pointer walking down.
func Contains(root *Node, v int) bool {
	for cur := root; cur != nil; {
		switch {
		case v == cur.Val:
			return true
		case v < cur.Val:
			cur = cur.Left
		default:
			cur = cur.Right
		}
	}
	return false
}

func InOrder(n *Node, visit func(int)) {
	if n == nil {
		return
	}
	InOrder(n.Left, visit)
	visit(n.Val)
	InOrder(n.Right, visit)
}

func main() {
	var root *Node
	for _, v := range []int{10, 5, 15, 3, 7, 12, 18} {
		root = Insert(root, v)
	}
	fmt.Println(Contains(root, 7), Contains(root, 8)) // true false
	InOrder(root, func(v int) { fmt.Print(v, " ") })  // 3 5 7 10 12 15 18
}
```

**Go specifics:**
- `&Node{}` returned from a function **escapes** to the heap. The compiler's escape analysis decides this (check with `go build -gcflags=-m`).
- The GC finds nodes by **tracing pointers from roots**. It walks your tree the same way you do. Free memory means "unreachable," so setting `root = nil` makes the whole tree collectible.
- 24 bytes is an exact Go allocator size class, so there is no wasted space per node.

## 7. Rust implementation

```rust
struct Node {
    val: i32,
    left: Option<Box<Node>>,
    right: Option<Box<Node>>,
}

type Tree = Option<Box<Node>>;

// `slot` is the *place* holding the pointer, so we can fill an empty one.
fn insert(slot: &mut Tree, v: i32) {
    match slot {
        None => {
            *slot = Some(Box::new(Node { val: v, left: None, right: None }))
        }
        Some(n) => {
            if v < n.val {
                insert(&mut n.left, v);
            } else if v > n.val {
                insert(&mut n.right, v);
            }
        }
    }
}

fn contains(root: &Tree, v: i32) -> bool {
    let mut cur = root; // a shared borrow walking down
    while let Some(n) = cur {
        if v == n.val {
            return true;
        }
        cur = if v < n.val { &n.left } else { &n.right };
    }
    false
}

fn in_order(t: &Tree, out: &mut Vec<i32>) {
    if let Some(n) = t {
        in_order(&n.left, out);
        out.push(n.val);
        in_order(&n.right, out);
    }
}

fn main() {
    let mut root: Tree = None;
    for v in [10, 5, 15, 3, 7, 12, 18] {
        insert(&mut root, v);
    }
    println!("{} {}", contains(&root, 7), contains(&root, 8)); // true false
    let mut out = Vec::new();
    in_order(&root, &mut out);
    println!("{:?}", out); // [3, 5, 7, 10, 12, 15, 18]
}
```

**Rust specifics:**
- `Box<Node>` is a single owning pointer to a heap allocation (8 bytes). `Option<Box<Node>>` is **also 8 bytes**, because `None` reuses the impossible address `0` (the *niche optimization*). It is the same memory as Go's `*Node`, but `None` is checked by the compiler.
- **Ownership = tree shape.** Each node has exactly one owner (its parent's `Box`). Cycles and shared children are impossible by construction. Parent pointers need `Rc<RefCell<>>`/`Weak` or indices.
- **Drop:** when `root` goes out of scope, Rust frees the nodes recursively, parent after children. Like recursion, this overflows the stack on very deep trees.
- `insert` takes `&mut Tree` (the *slot*), which avoids the "return the new root" pattern Go uses.

## 8. Common mistakes

| Mistake | Why it seems fine | Failure |
|---|---|---|
| Not checking `nil` before `.Val` | Works on non-empty trees | Panic on an empty tree or a leaf's child |
| Go: `func f(n *Node) { n = &Node{} }` | "I assigned the pointer" | The pointer is **copied**; the caller never sees it |
| Assuming BST means O(log n) | Average case is true | Sorted input gives height `n`, so O(n) |
| Rust: cloning subtrees to appease the borrow checker | It compiles | O(n) copy per operation |

## 9. Active recall (try before asking for answers)

1. In the 7-node tree above, how many pointer dereferences does `Contains(root, 18)` perform? What if the tree were a linked list of the same 7 values?
2. Why can Go's `Insert` return the root, but Rust's `insert` takes `&mut Tree` instead?
3. Inserting `1, 2, 3, ..., 100000` in order: what is the height, and what breaks first in Rust versus Go?
4. Why does `Option<Box<Node>>` cost 8 bytes and not 16?
5. If the array `[10, 5, 15, 3, 7, 12, 18]` stores the same tree by index, where are node `i`'s children? (Hint: no pointers needed.)

## 10. Practice

- **Beginner:** compute tree height recursively in both languages.
- **Intermediate:** write an *iterative* in-order traversal with an explicit stack (Go: slice; Rust: `Vec`).
- **Variation:** do level-order traversal. What data structure replaces the stack, and why?

Next, I can go deeper into one of these: BST deletion (pointer surgery), array-backed/arena trees, or why balancing (AVL/red-black) exists. Which one?

You didn't pick one, so I'll start with **BST deletion**. It's the clearest view of what "manipulating a tree" means at the memory level, and the other two topics (array-backed trees, balancing) are next if you want them.

## 1. Deletion is pointer surgery

To delete a node, you rewrite **the one pointer that points to it**. There are three cases:

```text
Case 1: LEAF            Case 2: ONE CHILD         Case 3: TWO CHILDREN
(delete 3)              (delete 15)               (delete 5)

    5                      10                         10
   / \                    /  \                       /  \
  3   7                  5    15                    5    15
                                \                  / \
  parent.left = nil              18               3   7

                         parent.right = 18      Can't hand two children to
                         (bypass the node)      one pointer slot. Use the
                                                SUCCESSOR instead.
```

**Case 3 trick:** the successor is the smallest node in the right subtree (go right once, then left until `nil`). It is guaranteed to have no left child, so removing it is case 1 or 2.

```text
Delete 5:                    Step 1: copy 7 into 5's node
    10                           10
   /  \                         /  \
  5    15                      7    15
 / \                          / \
3   7  <- successor          3   7  <- still there

                             Step 2: delete 7 from the right subtree
                                 10
                                /  \
                               7    15
                              /
                             3
```

The BST ordering holds because the successor is greater than everything on the left and no greater than anything else on the right.

## 2. Dry run: delete 5 (two children)

| Step | Pointer action | State |
|---|---|---|
| 1 | Walk root → left (5 < 10) | at node 5 |
| 2 | Both children non-nil → case 3 | |
| 3 | `s = minNode(5.right)` → node 7 | s = 7 |
| 4 | `5.Val = 7` | node reads 7 |
| 5 | Delete 7 from `5.right` (leaf → nil) | `right = nil` |

## 3. Go

```go
func minNode(n *Node) *Node {
	for n.Left != nil {
		n = n.Left
	}
	return n
}

func Delete(root *Node, v int) *Node {
	if root == nil {
		return nil
	}
	switch {
	case v < root.Val:
		root.Left = Delete(root.Left, v)
	case v > root.Val:
		root.Right = Delete(root.Right, v)
	default:
		if root.Left == nil {
			return root.Right // cases 1 and 2a
		}
		if root.Right == nil {
			return root.Left // case 2b
		}
		s := minNode(root.Right)
		root.Val = s.Val
		root.Right = Delete(root.Right, s.Val)
	}
	return root
}
```

- `return root.Right` is the "bypass": the caller assigns it into the parent's pointer field. Nobody frees anything.
- The removed node is now unreachable, so the GC reclaims it later.

## 4. Rust

```rust
// Remove and return the minimum value of a subtree.
fn pop_min(slot: &mut Tree) -> Option<i32> {
    if slot.as_ref()?.left.is_some() {
        pop_min(&mut slot.as_mut()?.left)
    } else {
        let node = slot.take()?; // slot becomes None, we own the Box
        *slot = node.right;      // splice right child into the slot
        Some(node.val)
    }
}

fn delete(slot: &mut Tree, v: i32) {
    let Some(n) = slot.as_mut() else { return };
    if v < n.val { return delete(&mut n.left, v); }
    if v > n.val { return delete(&mut n.right, v); }

    match (n.left.take(), n.right.take()) {
        (None, None) => *slot = None,
        (Some(l), None) => *slot = Some(l),
        (None, Some(r)) => *slot = Some(r),
        (Some(l), Some(r)) => {
            n.left = Some(l);
            n.right = Some(r);
            n.val = pop_min(&mut n.right).expect("right subtree is non-empty");
        }
    }
}
```

- `take()` swaps `None` into a place and hands you the old value. It is how you **move out of a borrowed slot**, which Rust otherwise forbids.
- `*slot = Some(l)` overwrites the slot, **dropping the old `Box<Node>` immediately**. The node is freed right there, deterministically. In Go, that happens whenever the GC runs.

I haven't compiled either snippet, so run them and tell me if the borrow checker complains.

## 5. Common mistakes

| Mistake | Failure |
|---|---|
| Copying the successor's value but not deleting the original | Duplicate key, tree is corrupted |
| Go: `root.Left = nil` for a node that has children | Whole subtree silently lost |
| Using `Delete` result without assigning it back (`Delete(root, 5)`) | Root deletion appears to do nothing |
| Using the predecessor in some places and the successor in others | Still valid, but the tree skews over time |

## 6. Active recall

1. Why is the successor guaranteed to have **no left child**?
2. Delete the root `10` from the 7-node tree. Draw the result.
3. What is the time complexity of deleting from a degenerate tree, and which part dominates?
4. Why does Rust's version need `take()` while Go's doesn't?

Try 1 and 2 first and send me your answers. I'll review them before we go on to balancing.