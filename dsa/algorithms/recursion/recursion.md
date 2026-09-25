I'll create a comprehensive guide to recursion implementation with complete examples in both Python and Rust. This will be a substantial reference document covering theory, patterns, and practical implementations.I've created a comprehensive guide to recursion implementation that covers both theoretical concepts and practical implementations in Python and Rust. The guide includes:

**Key Features:**
- **Theoretical Foundation**: Understanding recursion principles, anatomy, and common patterns
- **Complete Code Examples**: Working implementations in both Python and Rust
- **Progressive Complexity**: From basic factorial to advanced backtracking algorithms
- **Optimization Techniques**: Memoization, tail recursion, and performance considerations
- **Real-world Applications**: Binary trees, string processing, mathematical functions, and puzzle solving
- **Best Practices**: Error handling, testing strategies, and performance guidelines

**Covered Topics:**
1. Basic recursive patterns (factorial, Fibonacci)
2. Data structure operations (trees, lists, strings)
3. Mathematical algorithms (GCD, power functions, Tower of Hanoi)
4. Advanced patterns (mutual recursion, backtracking)
5. Performance optimization techniques
6. Language-specific considerations for Python and Rust

The guide provides both languages' implementations side-by-side, showing how recursion concepts translate between different programming paradigms. Each example includes proper error handling, optimization techniques where applicable, and clear explanations of the recursive strategy used.

Would you like me to expand on any particular section or add additional examples for specific use cases?

# Comprehensive Guide to Recursion Implementation

## Table of Contents
1. [Understanding Recursion](#understanding-recursion)
2. [Anatomy of Recursive Functions](#anatomy-of-recursive-functions)
3. [Common Recursion Patterns](#common-recursion-patterns)
4. [Python Implementations](#python-implementations)
5. [Rust Implementations](#rust-implementations)
6. [Optimization Techniques](#optimization-techniques)
7. [Advanced Patterns](#advanced-patterns)
8. [Performance Analysis](#performance-analysis)
9. [Best Practices](#best-practices)

## Understanding Recursion

Recursion is a programming technique where a function calls itself to solve a problem by breaking it down into smaller, similar subproblems. It's particularly powerful for problems that have a recursive structure, such as tree traversal, mathematical sequences, and divide-and-conquer algorithms.

### Key Concepts

- **Base Case**: The condition that stops the recursion
- **Recursive Case**: The function calling itself with modified parameters
- **Stack Frame**: Each function call creates a new frame on the call stack
- **Tail Recursion**: When the recursive call is the last operation in the function

## Anatomy of Recursive Functions

Every recursive function must have:

1. **One or more base cases** that terminate the recursion
2. **Recursive calls** that move toward the base case
3. **Progress toward termination** to avoid infinite recursion

```
function recursive_function(parameters):
    if base_case_condition:
        return base_case_value
    else:
        return recursive_function(modified_parameters)
```

## Common Recursion Patterns

### 1. Linear Recursion
Functions that make a single recursive call.

### 2. Binary Recursion
Functions that make two recursive calls (e.g., Fibonacci).

### 3. Multiple Recursion
Functions that make more than two recursive calls.

### 4. Mutual Recursion
Two or more functions calling each other recursively.

### 5. Tail Recursion
The recursive call is the last operation performed.

## Python Implementations

### Basic Examples

#### 1. Factorial

```python
def factorial(n):
    """Calculate n! using recursion."""
    if n <= 1:
        return 1
    return n * factorial(n - 1)

# Tail-recursive version
def factorial_tail(n, acc=1):
    """Tail-recursive factorial implementation."""
    if n <= 1:
        return acc
    return factorial_tail(n - 1, n * acc)

# Test
print(f"5! = {factorial(5)}")  # Output: 120
print(f"5! = {factorial_tail(5)}")  # Output: 120
```

#### 2. Fibonacci Sequence

```python
def fibonacci(n):
    """Basic recursive Fibonacci (inefficient)."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

# Memoized version
def fibonacci_memo(n, memo={}):
    """Fibonacci with memoization for efficiency."""
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    memo[n] = fibonacci_memo(n - 1, memo) + fibonacci_memo(n - 2, memo)
    return memo[n]

# Using functools.lru_cache
from functools import lru_cache

@lru_cache(maxsize=None)
def fibonacci_cached(n):
    """Fibonacci with automatic caching."""
    if n <= 1:
        return n
    return fibonacci_cached(n - 1) + fibonacci_cached(n - 2)

# Test
print(f"Fibonacci(10) = {fibonacci_memo(10)}")  # Output: 55
```

#### 3. Binary Tree Operations

```python
class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

def tree_height(root):
    """Calculate the height of a binary tree."""
    if not root:
        return 0
    return 1 + max(tree_height(root.left), tree_height(root.right))

def inorder_traversal(root, result=None):
    """In-order traversal of binary tree."""
    if result is None:
        result = []
    
    if root:
        inorder_traversal(root.left, result)
        result.append(root.val)
        inorder_traversal(root.right, result)
    
    return result

def tree_sum(root):
    """Calculate sum of all nodes in binary tree."""
    if not root:
        return 0
    return root.val + tree_sum(root.left) + tree_sum(root.right)

# Example usage
root = TreeNode(1)
root.left = TreeNode(2)
root.right = TreeNode(3)
root.left.left = TreeNode(4)
root.left.right = TreeNode(5)

print(f"Tree height: {tree_height(root)}")  # Output: 3
print(f"Inorder: {inorder_traversal(root)}")  # Output: [4, 2, 5, 1, 3]
print(f"Tree sum: {tree_sum(root)}")  # Output: 15
```

#### 4. List Operations

```python
def list_sum(lst):
    """Sum all elements in a list recursively."""
    if not lst:
        return 0
    return lst[0] + list_sum(lst[1:])

def reverse_list(lst):
    """Reverse a list recursively."""
    if len(lst) <= 1:
        return lst
    return [lst[-1]] + reverse_list(lst[:-1])

def find_max(lst):
    """Find maximum element recursively."""
    if len(lst) == 1:
        return lst[0]
    
    max_rest = find_max(lst[1:])
    return lst[0] if lst[0] > max_rest else max_rest

# Test
numbers = [3, 7, 2, 9, 1]
print(f"Sum: {list_sum(numbers)}")  # Output: 22
print(f"Reversed: {reverse_list(numbers)}")  # Output: [1, 9, 2, 7, 3]
print(f"Max: {find_max(numbers)}")  # Output: 9
```

#### 5. String Operations

```python
def is_palindrome(s):
    """Check if string is palindrome recursively."""
    s = s.lower().replace(' ', '')
    
    if len(s) <= 1:
        return True
    
    if s[0] != s[-1]:
        return False
    
    return is_palindrome(s[1:-1])

def string_reverse(s):
    """Reverse string recursively."""
    if len(s) <= 1:
        return s
    return s[-1] + string_reverse(s[:-1])

def count_char(s, char):
    """Count occurrences of character recursively."""
    if not s:
        return 0
    
    count = 1 if s[0] == char else 0
    return count + count_char(s[1:], char)

# Test
print(f"Is 'racecar' palindrome? {is_palindrome('racecar')}")  # True
print(f"Reverse 'hello': {string_reverse('hello')}")  # 'olleh'
print(f"Count 'l' in 'hello': {count_char('hello', 'l')}")  # 2
```

#### 6. Mathematical Functions

```python
def power(base, exp):
    """Calculate base^exp recursively."""
    if exp == 0:
        return 1
    if exp == 1:
        return base
    
    # Optimization for even exponents
    if exp % 2 == 0:
        half_power = power(base, exp // 2)
        return half_power * half_power
    else:
        return base * power(base, exp - 1)

def gcd(a, b):
    """Greatest Common Divisor using Euclidean algorithm."""
    if b == 0:
        return a
    return gcd(b, a % b)

def tower_of_hanoi(n, source, destination, auxiliary):
    """Solve Tower of Hanoi puzzle."""
    if n == 1:
        print(f"Move disk 1 from {source} to {destination}")
        return
    
    tower_of_hanoi(n - 1, source, auxiliary, destination)
    print(f"Move disk {n} from {source} to {destination}")
    tower_of_hanoi(n - 1, auxiliary, destination, source)

# Test
print(f"2^10 = {power(2, 10)}")  # 1024
print(f"gcd(48, 18) = {gcd(48, 18)}")  # 6
tower_of_hanoi(3, 'A', 'C', 'B')
```

## Rust Implementations

### Basic Examples

#### 1. Factorial

```rust
fn factorial(n: u64) -> u64 {
    match n {
        0 | 1 => 1,
        _ => n * factorial(n - 1),
    }
}

// Tail-recursive version
fn factorial_tail(n: u64, acc: u64) -> u64 {
    match n {
        0 | 1 => acc,
        _ => factorial_tail(n - 1, n * acc),
    }
}

// Wrapper function for cleaner API
fn factorial_optimized(n: u64) -> u64 {
    factorial_tail(n, 1)
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_factorial() {
        assert_eq!(factorial(5), 120);
        assert_eq!(factorial_optimized(5), 120);
    }
}
```

#### 2. Fibonacci Sequence

```rust
fn fibonacci(n: u32) -> u64 {
    match n {
        0 => 0,
        1 => 1,
        _ => fibonacci(n - 1) + fibonacci(n - 2),
    }
}

// Memoized version using HashMap
use std::collections::HashMap;

fn fibonacci_memo(n: u32, memo: &mut HashMap<u32, u64>) -> u64 {
    if let Some(&result) = memo.get(&n) {
        return result;
    }
    
    let result = match n {
        0 => 0,
        1 => 1,
        _ => fibonacci_memo(n - 1, memo) + fibonacci_memo(n - 2, memo),
    };
    
    memo.insert(n, result);
    result
}

// Wrapper function
fn fibonacci_optimized(n: u32) -> u64 {
    let mut memo = HashMap::new();
    fibonacci_memo(n, &mut memo)
}
```

#### 3. Binary Tree Operations

```rust
#[derive(Debug, PartialEq, Eq)]
pub struct TreeNode {
    pub val: i32,
    pub left: Option<Box<TreeNode>>,
    pub right: Option<Box<TreeNode>>,
}

impl TreeNode {
    pub fn new(val: i32) -> Self {
        TreeNode {
            val,
            left: None,
            right: None,
        }
    }
}

fn tree_height(root: &Option<Box<TreeNode>>) -> i32 {
    match root {
        None => 0,
        Some(node) => {
            1 + std::cmp::max(
                tree_height(&node.left),
                tree_height(&node.right)
            )
        }
    }
}

fn inorder_traversal(root: &Option<Box<TreeNode>>, result: &mut Vec<i32>) {
    if let Some(node) = root {
        inorder_traversal(&node.left, result);
        result.push(node.val);
        inorder_traversal(&node.right, result);
    }
}

fn tree_sum(root: &Option<Box<TreeNode>>) -> i32 {
    match root {
        None => 0,
        Some(node) => {
            node.val + tree_sum(&node.left) + tree_sum(&node.right)
        }
    }
}
```

#### 4. List Operations

```rust
fn list_sum(lst: &[i32]) -> i32 {
    match lst {
        [] => 0,
        [head, tail @ ..] => head + list_sum(tail),
    }
}

fn reverse_vec(vec: Vec<i32>) -> Vec<i32> {
    fn reverse_helper(vec: Vec<i32>, acc: Vec<i32>) -> Vec<i32> {
        match vec.split_first() {
            None => acc,
            Some((head, tail)) => {
                let mut new_acc = vec![*head];
                new_acc.extend(acc);
                reverse_helper(tail.to_vec(), new_acc)
            }
        }
    }
    reverse_helper(vec, Vec::new())
}

fn find_max(lst: &[i32]) -> Option<i32> {
    match lst {
        [] => None,
        [single] => Some(*single),
        [head, tail @ ..] => {
            let max_tail = find_max(tail);
            match max_tail {
                None => Some(*head),
                Some(max_val) => Some(if *head > max_val { *head } else { max_val }),
            }
        }
    }
}
```

#### 5. String Operations

```rust
fn is_palindrome(s: &str) -> bool {
    let chars: Vec<char> = s.chars().collect();
    is_palindrome_chars(&chars)
}

fn is_palindrome_chars(chars: &[char]) -> bool {
    match chars.len() {
        0 | 1 => true,
        _ => {
            chars[0] == chars[chars.len() - 1] && 
            is_palindrome_chars(&chars[1..chars.len() - 1])
        }
    }
}

fn string_reverse(s: &str) -> String {
    match s.chars().next() {
        None => String::new(),
        Some(first) => {
            let rest: String = s.chars().skip(1).collect();
            string_reverse(&rest) + &first.to_string()
        }
    }
}

fn count_char(s: &str, target: char) -> usize {
    match s.chars().next() {
        None => 0,
        Some(first) => {
            let rest: String = s.chars().skip(1).collect();
            let count = if first == target { 1 } else { 0 };
            count + count_char(&rest, target)
        }
    }
}
```

#### 6. Mathematical Functions

```rust
fn power(base: i64, exp: u32) -> i64 {
    match exp {
        0 => 1,
        1 => base,
        n if n % 2 == 0 => {
            let half_power = power(base, n / 2);
            half_power * half_power
        }
        _ => base * power(base, exp - 1),
    }
}

fn gcd(a: u64, b: u64) -> u64 {
    match b {
        0 => a,
        _ => gcd(b, a % b),
    }
}

fn tower_of_hanoi(n: u32, source: &str, destination: &str, auxiliary: &str) {
    if n == 1 {
        println!("Move disk 1 from {} to {}", source, destination);
        return;
    }
    
    tower_of_hanoi(n - 1, source, auxiliary, destination);
    println!("Move disk {} from {} to {}", n, source, destination);
    tower_of_hanoi(n - 1, auxiliary, destination, source);
}
```

## Optimization Techniques

### 1. Memoization
Store previously computed results to avoid redundant calculations.

**Python Example:**
```python
from functools import lru_cache

@lru_cache(maxsize=None)
def expensive_recursive_function(n):
    # Implementation
    pass
```

**Rust Example:**
```rust
use std::collections::HashMap;

fn with_memoization(n: u32, cache: &mut HashMap<u32, u64>) -> u64 {
    if let Some(&result) = cache.get(&n) {
        return result;
    }
    // Compute result and store in cache
    let result = compute_result(n);
    cache.insert(n, result);
    result
}
```

### 2. Tail Call Optimization
Convert to tail-recursive form when possible.

### 3. Iterative Conversion
Some recursive functions can be converted to iterative versions for better performance.

## Advanced Patterns

### 1. Mutual Recursion

**Python:**
```python
def is_even(n):
    if n == 0:
        return True
    return is_odd(n - 1)

def is_odd(n):
    if n == 0:
        return False
    return is_even(n - 1)
```

**Rust:**
```rust
fn is_even(n: u32) -> bool {
    match n {
        0 => true,
        _ => is_odd(n - 1),
    }
}

fn is_odd(n: u32) -> bool {
    match n {
        0 => false,
        _ => is_even(n - 1),
    }
}
```

### 2. Tree-based Recursion

Useful for parsing, decision trees, and hierarchical data structures.

### 3. Backtracking

**Python N-Queens Example:**
```python
def solve_n_queens(n):
    def is_safe(board, row, col):
        # Check column and diagonals
        for i in range(row):
            if (board[i] == col or 
                board[i] - i == col - row or 
                board[i] + i == col + row):
                return False
        return True
    
    def backtrack(board, row):
        if row == n:
            return [board[:]]
        
        solutions = []
        for col in range(n):
            if is_safe(board, row, col):
                board[row] = col
                solutions.extend(backtrack(board, row + 1))
        return solutions
    
    return backtrack([0] * n, 0)
```

## Performance Analysis

### Time Complexity
- **Linear recursion**: Usually O(n)
- **Binary recursion (naive)**: Often O(2^n) - exponential
- **With memoization**: Typically O(n) space and time

### Space Complexity
- **Call stack**: O(depth of recursion)
- **Memoization**: Additional O(n) for cache storage

### Memory Considerations
- Each recursive call consumes stack space
- Deep recursion can cause stack overflow
- Tail recursion optimization can help (language dependent)

## Best Practices

### 1. Always Define Clear Base Cases
Ensure recursion terminates properly.

### 2. Make Progress Toward Base Case
Each recursive call should get closer to termination.

### 3. Consider Stack Depth Limits
Be aware of maximum recursion depth limitations.

**Python:**
```python
import sys
sys.setrecursionlimit(10000)  # Increase if needed
```

### 4. Use Memoization for Overlapping Subproblems
Avoid redundant calculations in problems like Fibonacci.

### 5. Choose Appropriate Data Structures
Use efficient data structures for your recursive algorithms.

### 6. Error Handling
Always validate input parameters and handle edge cases.

**Python:**
```python
def safe_factorial(n):
    if not isinstance(n, int) or n < 0:
        raise ValueError("Input must be a non-negative integer")
    return factorial(n)
```

**Rust:**
```rust
fn safe_factorial(n: i32) -> Result<u64, &'static str> {
    match n {
        n if n < 0 => Err("Input must be non-negative"),
        n => Ok(factorial(n as u64)),
    }
}
```

### 7. Consider Iterative Alternatives
For simple cases, iterative solutions might be more efficient.

### 8. Profile and Benchmark
Measure performance for critical recursive functions.

### 9. Documentation
Document the recursive strategy, base cases, and time/space complexity.

### 10. Testing
Test with various inputs including edge cases, empty inputs, and boundary conditions.

## Conclusion

Recursion is a powerful programming technique that can lead to elegant and intuitive solutions for many problems. The key to mastering recursion is understanding the problem structure, identifying appropriate base cases, and ensuring progress toward termination. Both Python and Rust provide excellent support for recursive programming, with their own unique advantages and considerations.

Remember to always consider the trade-offs between recursive and iterative solutions, and use optimization techniques like memoization when dealing with overlapping subproblems. With practice and understanding of these principles, you'll be able to effectively apply recursion to solve complex problems in both languages.

I'll create a detailed ASCII diagram showing how recursion works using the classic factorial example. This will illustrate the call stack, parameter passing, and return values.This ASCII diagram illustrates recursion using the factorial function as an example. Here are the key points it demonstrates:

**Phase 1 - Building the Call Stack**: Shows how each recursive call creates a new stack frame, with parameters getting smaller until reaching the base case.

**Phase 2 - Unwinding**: Demonstrates how the results bubble back up through the call stack, with each level multiplying its parameter by the result from the level below.

**Visual Elements**:
- Boxes show each function call with its parameters and logic
- Arrows indicate the flow of calls and returns
- Stack visualization shows memory usage at different phases
- Trace table summarizes the entire process

The diagram emphasizes three critical aspects of recursion:
1. **Base Case**: The stopping condition (factorial(0) = 1)
2. **Recursive Case**: How the function calls itself with simpler input
3. **Stack Management**: How the computer manages multiple function calls waiting to complete

This pattern applies to many recursive algorithms - the key is always having a base case to prevent infinite recursion and ensuring each recursive call moves toward that base case.

RECURSION EXPLAINED: factorial(4) Step-by-Step
========================================================

CONCEPT: Recursion is when a function calls itself with a simpler version of the problem
BASE CASE: The condition that stops the recursion (factorial(0) = 1)
RECURSIVE CASE: The function calling itself with modified parameters

FUNCTION DEFINITION:
def factorial(n):
    if n == 0:          # Base case
        return 1
    else:               # Recursive case
        return n * factorial(n-1)

========================================================
STEP-BY-STEP EXECUTION OF factorial(4):
========================================================

PHASE 1: BUILDING THE CALL STACK (Going Down)
════════════════════════════════════════════════════════

Call 1: factorial(4)
┌─────────────────────────────────┐
│ factorial(4)                    │  ← Initial call
│ n = 4                          │
│ n != 0, so calculate:          │
│ return 4 * factorial(3)        │  ← Calls factorial(3)
└─────────────────────────────────┘
                │
                ▼ CALLS
                
Call 2: factorial(3)
┌─────────────────────────────────┐
│ factorial(3)                    │  ← Called by factorial(4)
│ n = 3                          │
│ n != 0, so calculate:          │
│ return 3 * factorial(2)        │  ← Calls factorial(2)
└─────────────────────────────────┘
                │
                ▼ CALLS
                
Call 3: factorial(2)
┌─────────────────────────────────┐
│ factorial(2)                    │  ← Called by factorial(3)
│ n = 2                          │
│ n != 0, so calculate:          │
│ return 2 * factorial(1)        │  ← Calls factorial(1)
└─────────────────────────────────┘
                │
                ▼ CALLS
                
Call 4: factorial(1)
┌─────────────────────────────────┐
│ factorial(1)                    │  ← Called by factorial(2)
│ n = 1                          │
│ n != 0, so calculate:          │
│ return 1 * factorial(0)        │  ← Calls factorial(0)
└─────────────────────────────────┘
                │
                ▼ CALLS
                
Call 5: factorial(0)
┌─────────────────────────────────┐
│ factorial(0)                    │  ← Called by factorial(1)
│ n = 0                          │
│ BASE CASE REACHED!             │
│ return 1                       │  ← Returns 1 (no more calls)
└─────────────────────────────────┘

CALL STACK AT MAXIMUM DEPTH:
════════════════════════════════════
│ factorial(0) │ ← Top of stack
├──────────────┤
│ factorial(1) │
├──────────────┤
│ factorial(2) │
├──────────────┤
│ factorial(3) │
├──────────────┤
│ factorial(4) │ ← Bottom of stack (original call)
└──────────────┘

========================================================
PHASE 2: UNWINDING THE STACK (Going Back Up)
════════════════════════════════════════════════════════

Return 5: factorial(0) → factorial(1)
┌─────────────────────────────────┐
│ factorial(0) returns 1          │  ← BASE CASE
└─────────────────────────────────┘
                │
                ▼ RETURNS 1
┌─────────────────────────────────┐
│ factorial(1)                    │
│ return 1 * factorial(0)        │
│ return 1 * 1                   │
│ return 1                       │  ← Returns to factorial(2)
└─────────────────────────────────┘

Return 4: factorial(1) → factorial(2)
┌─────────────────────────────────┐
│ factorial(1) returns 1          │
└─────────────────────────────────┘
                │
                ▼ RETURNS 1
┌─────────────────────────────────┐
│ factorial(2)                    │
│ return 2 * factorial(1)        │
│ return 2 * 1                   │
│ return 2                       │  ← Returns to factorial(3)
└─────────────────────────────────┘

Return 3: factorial(2) → factorial(3)
┌─────────────────────────────────┐
│ factorial(2) returns 2          │
└─────────────────────────────────┘
                │
                ▼ RETURNS 2
┌─────────────────────────────────┐
│ factorial(3)                    │
│ return 3 * factorial(2)        │
│ return 3 * 2                   │
│ return 6                       │  ← Returns to factorial(4)
└─────────────────────────────────┘

Return 2: factorial(3) → factorial(4)
┌─────────────────────────────────┐
│ factorial(3) returns 6          │
└─────────────────────────────────┘
                │
                ▼ RETURNS 6
┌─────────────────────────────────┐
│ factorial(4)                    │
│ return 4 * factorial(3)        │
│ return 4 * 6                   │
│ return 24                      │  ← Final result!
└─────────────────────────────────┘

FINAL RESULT: factorial(4) = 24

========================================================
MEMORY VISUALIZATION DURING EXECUTION:
========================================================

Time →  [Start]  [Max Depth]  [Unwinding]  [Complete]
        
Stack:     │         │           │            │
          │         │factorial(0) │            │
          │         │factorial(1) │factorial(1) │
          │         │factorial(2) │factorial(2) │
          │         │factorial(3) │factorial(3) │
    factorial(4)   factorial(4)   factorial(4)  factorial(4)
        ↓           ↓             ↑            ↑
    Calling...   Building up    Returning    Result: 24

========================================================
KEY RECURSION CONCEPTS ILLUSTRATED:
========================================================

1. SELF-REFERENCE: Each function calls itself with different parameters
   factorial(n) → factorial(n-1)

2. BASE CASE: Stops infinite recursion
   factorial(0) returns 1 without calling itself

3. CALL STACK: Each call waits for the next to complete
   ┌─ factorial(4) waits for factorial(3)
   ├─ factorial(3) waits for factorial(2) 
   ├─ factorial(2) waits for factorial(1)
   ├─ factorial(1) waits for factorial(0)
   └─ factorial(0) returns immediately

4. UNWINDING: Results bubble back up
   1 → 1 → 2 → 6 → 24

5. DIVIDE & CONQUER: Big problem broken into smaller identical problems
   factorial(4) = 4 × factorial(3) = 4 × 3 × factorial(2) = ... = 4 × 3 × 2 × 1

========================================================
TRACE TABLE:
========================================================
Call Level │ Input │ Calculation      │ Returns
───────────┼───────┼──────────────────┼─────────
     1     │   4   │ 4 * factorial(3) │   24
     2     │   3   │ 3 * factorial(2) │    6
     3     │   2   │ 2 * factorial(1) │    2
     4     │   1   │ 1 * factorial(0) │    1
     5     │   0   │ Base case        │    1


# Types of Recursion — with Recursion Trees (Go & Rust)

## 1. Linear Recursion
Each call makes **exactly one** recursive call. The call stack forms a straight line, not a branching tree.

**Example: Factorial**

```go
func factorial(n int) int {
    if n == 0 {
        return 1
    }
    return n * factorial(n-1) // single recursive call
}
```

```rust
fn factorial(n: u64) -> u64 {
    if n == 0 {
        1
    } else {
        n * factorial(n - 1) // single recursive call
    }
}
```

**Recursion "tree" (really a chain) for `factorial(4)`:**
```
factorial(4)
  └─ factorial(3)
       └─ factorial(2)
            └─ factorial(1)
                 └─ factorial(0) → returns 1
            ← 1*1 = 1
       ← 2*1 = 2
  ← 3*2 = 6
← 4*6 = 24
```
**Complexity:** O(n) time, O(n) stack space (depth = n).

---

## 2. Binary Recursion
Each call makes **two** recursive calls. This creates a true branching tree — and often redundant work if not memoized.

**Example: Naive Fibonacci**

```go
func fib(n int) int {
    if n <= 1 {
        return n
    }
    return fib(n-1) + fib(n-2) // two recursive calls
}
```

```rust
fn fib(n: u64) -> u64 {
    if n <= 1 {
        n
    } else {
        fib(n - 1) + fib(n - 2) // two recursive calls
    }
}
```

**Recursion tree for `fib(5)`:**
```
                        fib(5)
                      /        \
                 fib(4)          fib(3)
                /      \        /      \
           fib(3)     fib(2) fib(2)   fib(1)
          /     \     /   \   /   \
     fib(2)  fib(1) fib(1)fib(0)fib(1)fib(0)
     /   \
  fib(1) fib(0)
```
Notice `fib(3)`, `fib(2)`, etc. get **recomputed** many times — this is why naive binary recursion is O(2ⁿ). (Memoization / DP collapses this tree into a line, giving O(n).)

**Complexity:** O(2ⁿ) time, O(n) space (max stack depth, since only one branch is "live" at a time).

---

## 3. Multiple Recursion
Each call makes **more than two** recursive calls. Common in combinatorics, grid/graph traversal, and divide-and-conquer over multiple partitions.

**Example: Counting ways to climb stairs taking 1, 2, or 3 steps**

```go
func countWays(n int) int {
    if n < 0 {
        return 0
    }
    if n == 0 {
        return 1
    }
    return countWays(n-1) + countWays(n-2) + countWays(n-3) // 3 calls
}
```

```rust
fn count_ways(n: i32) -> i32 {
    if n < 0 {
        return 0;
    }
    if n == 0 {
        return 1;
    }
    count_ways(n - 1) + count_ways(n - 2) + count_ways(n - 3) // 3 calls
}
```

**Recursion tree for `countWays(4)`** (each node branches into 3):
```
                     countWays(4)
              /            |            \
       countWays(3)  countWays(2)  countWays(1)
       /   |   \        /   \           |
   cw(2) cw(1) cw(0) cw(1) cw(0)      cw(0)
   / | \   |          |
 cw1 cw0 cw-1 cw0   cw0
 ...
```
**Complexity:** O(3ⁿ) time in the naive form — grows even faster than binary recursion since branching factor is 3.

---

## 4. Mutual Recursion
Two or more functions call **each other**, rather than calling themselves directly.

**Example: `isEven` / `isOdd`**

```go
func isEven(n int) bool {
    if n == 0 {
        return true
    }
    return isOdd(n - 1)
}

func isOdd(n int) bool {
    if n == 0 {
        return false
    }
    return isEven(n - 1)
}
```

```rust
fn is_even(n: u32) -> bool {
    if n == 0 {
        true
    } else {
        is_odd(n - 1)
    }
}

fn is_odd(n: u32) -> bool {
    if n == 0 {
        false
    } else {
        is_even(n - 1)
    }
}
```

**"Tree" (alternating chain) for `isEven(4)`:**
```
isEven(4)
  └─ isOdd(3)
       └─ isEven(2)
            └─ isOdd(1)
                 └─ isEven(0) → true
```
Note the function name **flips at each level** — that's the signature of mutual recursion. It's still linear in shape (one call per level), just alternating identity.

**Complexity:** O(n) time, O(n) space.

---

## 5. Tail Recursion
The recursive call is the **very last action** — nothing happens after it returns (no pending multiplication, addition, etc.). This allows compilers/runtimes that support **Tail Call Optimization (TCO)** to reuse the current stack frame instead of pushing a new one.

**Example: Tail-recursive factorial (using an accumulator)**

```go
// Note: Go does NOT perform TCO, so this still uses O(n) stack frames,
// but it demonstrates the tail-recursive *pattern*.
func factorialTail(n int, acc int) int {
    if n == 0 {
        return acc
    }
    return factorialTail(n-1, n*acc) // last operation, nothing after it
}
```

```rust
// Rust also does not guarantee TCO (LLVM may optimize it in release
// builds, but it's not language-guaranteed), but the pattern is idiomatic.
fn factorial_tail(n: u64, acc: u64) -> u64 {
    if n == 0 {
        acc
    } else {
        factorial_tail(n - 1, n * acc) // last operation, nothing after it
    }
}
```

**Recursion tree/chain for `factorialTail(4, 1)`:**
```
factorialTail(4, 1)
  └─ factorialTail(3, 4)
       └─ factorialTail(2, 12)
            └─ factorialTail(1, 24)
                 └─ factorialTail(0, 24) → returns 24 directly
```
Compare this to plain `factorial`: there, each frame waits for its child to return, then multiplies (`n * factorial(n-1)`) — that multiplication *after* the call is what makes it **non-tail** recursive. Here, the multiplication happens **before** the call, packed into the accumulator, so the call is the last thing that happens.

**Complexity:** O(n) time. With true TCO, O(1) stack space; without it (as in Go/Rust by default), still O(n) space.

---

## Quick Comparison Table

| Type | # recursive calls | Shape | Example | Time (naive) |
|---|---|---|---|---|
| Linear | 1 | chain | factorial | O(n) |
| Binary | 2 | binary tree | fibonacci | O(2ⁿ) |
| Multiple | >2 | k-ary tree | stair-climbing (k steps) | O(kⁿ) |
| Mutual | 1, alternating functions | chain, alternating labels | isEven/isOdd | O(n) |
| Tail | 1, as last op | chain, but stack-reusable | factorial w/ accumulator | O(n), O(1) space with TCO |

A practical note since you're doing DSA in Go/Rust: **neither language guarantees TCO** (unlike Scheme/Erlang), so for deep recursion (large n) on real inputs, prefer converting tail-recursive solutions into iterative loops to avoid stack overflow — the accumulator pattern above translates directly into a `for` loop.

Want me to go through converting one of these (e.g., binary recursion → memoized DP, or tail recursion → iterative loop) with the code side by side?