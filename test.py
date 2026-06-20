from src.finverisql.schema_loader import SchemaAnnotationStore


def main() -> None:
    store = SchemaAnnotationStore.from_json("data/booksql/schema_annotations.json")

    schema_reference = store.render_compact_schema_reference(
        include_technical_columns=False,
        include_low_value_attributes=False,
    )

    print(schema_reference)
    print()
    print("=" * 100)
    print(f"Character count: {len(schema_reference)}")


if __name__ == "__main__":
    main()