# Source-anchor format normalization

## Problem

The Controller rejects a Teacher source-evidence anchor when it names the
correct C++ function but differs from the Repository Graph declarator only in
whitespace beside C++ punctuation.  In AES R5, the graph emitted a multi-line
declarator with a space after `(` while the Teacher supplied the conventional
no-space spelling.  The existing resolver already ignores whitespace beside
`*`, but not beside other declarator punctuation.

## Decision

Normalize whitespace only adjacent to C++ declarator punctuation—parentheses,
commas, pointer/reference markers, brackets, and scope separators—before the
existing exact declarator comparison.  Construct qualified declarators by
splitting the function-name prefix before the parameter list, rather than at a
scope separator that may appear inside a parameter type.  Preserve identifiers,
type tokens, template contents, qualifiers, and parameter order.  The resolver
must still return `ambiguous` for a bare overloaded name and must not match a
different parameter list.  Treat contiguous `&&` and `::` as lexical tokens
and never merge their whitespace-separated component characters.

## Scope and verification

Add a regression test using the AES-shaped fully-qualified methods with
pointer/reference parameters, where source and requested anchors differ only
in punctuation-adjacent whitespace and parameter types include scope
separators.  Verify the test is red before the change, green after it, and
retain the existing overload ambiguity test as the guard against an unsafe
relaxation.
