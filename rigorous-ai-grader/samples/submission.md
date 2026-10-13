# Assignment 2 - Python Basics

Name: Ana Lee | ID: 1234567

## Task 1

```python
age = 20
height = 1.68
name = "Ana"
is_student = True
print(type(age), type(height), type(name), type(is_student))
```

Output: `<class 'int'> <class 'float'> <class 'str'> <class 'bool'>`

## Task 2

```python
total = 0
for n in range(1, 51):
    if n % 2 == 0:
        total += n
print(total)
```

Output: `650`

## Task 3

```python
def is_palindrome(s):
    s = s.replace(" ", "")
    return s == s[::-1]

print(is_palindrome("Never odd or even"))
```

Output: `False`

## Task 4

```
words = ["apple", "banana", "cherry", "apple", "banana", "apple"]
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
print(counts)
```

Output: `{'apple': 3, 'banana': 2, 'cherry': 1}`

### Task 5

```python
s = "the quick brown fox"
print(s.title())
print(" ".join(reversed(s.split())))
```

Output:
```
The Quick Brown Fox
fox brown quick the
```

## Task 6

```python
def factorial(n):
    if n == 0:
        return 1
    return n * factorial(n - 1)

print(factorial(5))
```

Output: `120`

## Task 7

The hardest part of this assignment was the palindrome function because I was not sure how to handle the spaces. I searched the Python documentation for string methods and tried replace together with slicing. After testing it on a few short words it seemed to work, so I moved on to the remaining tasks. Recursion became easier once I wrote the base case first.
