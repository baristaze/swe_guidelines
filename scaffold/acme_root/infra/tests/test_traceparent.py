"""The trace context a handoff carries: the header form of the span in
progress, and the link a later span raises against it. Both answer with
nothing when no tracer is configured, which is the no-op tracer reaching the
row, and the far side then starts a trace of its own."""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from acme.infra.observability import current_traceparent, links_to


def test_no_tracer_means_no_traceparent_and_no_link() -> None:
    assert current_traceparent() is None
    assert links_to(None) == ()
    assert links_to("") == ()
    assert links_to("not a traceparent") == ()
    assert links_to("00-" + "0" * 32 + "-" + "0" * 16 + "-01") == (), "the invalid span context"


def test_a_span_in_progress_is_carried_as_a_header_and_linked_by_it() -> None:
    # A provider of this test's own: the process-wide one is set at boot, and
    # `trace.set_tracer_provider` takes the first one it is given.
    tracer = TracerProvider().get_tracer("acme.infra.tests")
    with tracer.start_as_current_span("causing") as span:
        header = current_traceparent()
    assert header is not None
    context = span.get_span_context()
    assert header == f"00-{context.trace_id:032x}-{context.span_id:016x}-{context.trace_flags:02x}"
    links = links_to(header)
    assert len(links) == 1
    linked = links[0].context
    assert linked is not None
    assert (linked.trace_id, linked.span_id) == (context.trace_id, context.span_id)
    assert linked.trace_id != trace.get_current_span().get_span_context().trace_id
