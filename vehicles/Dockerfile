# Demo vehicle image: F Prime's Ref deployment built at two variants (A and B), plus a
# headless ground data system (fprime-gds) to talk to it.
#
# Variant A = Ref at the pinned F Prime tag + base.patch (adds an Fpy sequencer and reports
#             the dictionary hash in a custom version channel).
# Variant B = variant A + variant_b.patch (changes the type of one command argument).
#
# Targets:
#   dictionaries  the two dictionaries only (`docker build --target dictionaries --output ...`)
#   vehicle       the runnable image (default)

ARG FPRIME_TAG=v4.3.0

FROM ubuntu:24.04 AS toolchain
ARG FPRIME_TAG
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates cmake git python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${FPRIME_TAG}" https://github.com/nasa/fprime.git /opt/fprime
RUN python3 -m venv /opt/fprime-venv \
    && /opt/fprime-venv/bin/pip install --no-cache-dir -r /opt/fprime/requirements.txt
ENV PATH="/opt/fprime-venv/bin:${PATH}"

FROM toolchain AS build
ARG BUILD_JOBS=6
ENV BUILD_JOBS=${BUILD_JOBS}
COPY build_variants/ /opt/build_variants/
RUN /opt/build_variants/build.sh A gate-demo-1.0.0 /opt/build_variants/base.patch
RUN /opt/build_variants/build.sh B gate-demo-1.1.0 /opt/build_variants/variant_b.patch

FROM scratch AS dictionaries
COPY --from=build /opt/builds/A/RefTopologyDictionary.json /A/RefTopologyDictionary.json
COPY --from=build /opt/builds/A/build.json /A/build.json
COPY --from=build /opt/builds/B/RefTopologyDictionary.json /B/RefTopologyDictionary.json
COPY --from=build /opt/builds/B/build.json /B/build.json

FROM ubuntu:24.04 AS vehicle
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/gds-venv \
    && /opt/gds-venv/bin/pip install --no-cache-dir fprime-gds==4.3.1
ENV PATH="/opt/gds-venv/bin:${PATH}"
COPY --from=build /opt/builds /opt/builds
COPY vehicle-entrypoint.sh /usr/local/bin/vehicle-entrypoint
COPY reflash.sh /usr/local/bin/reflash
RUN chmod +x /usr/local/bin/vehicle-entrypoint /usr/local/bin/reflash
ENV VEHICLE_BUILD=A
EXPOSE 50050
ENTRYPOINT ["/usr/local/bin/vehicle-entrypoint"]
