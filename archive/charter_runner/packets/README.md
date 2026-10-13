# Charter-runner packet templates (Phase 0)

Markdown stubs for generate, autonomous, consult, and closed-detent packet
bodies. Each template ends with a fenced ``charter-state`` footer placeholder.
Materializers dual-write the live footer via ``checkpoint_schema.append_footer_to_packet``
until Phase 3 cutover makes this directory the sole packet-body source.
