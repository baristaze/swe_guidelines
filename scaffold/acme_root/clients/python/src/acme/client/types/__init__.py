"""The facade over the generated `schema`: one module per namespace, named
as its document is, holding the names consumers import. Nothing outside this
package imports `schema` directly, so a regeneration that renames a generated
class is absorbed here."""
