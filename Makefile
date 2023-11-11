CRD=crd.yaml
IMAGE=pguser-operator


# HELP
# This will output the help for each task
# thanks to https://marmelab.com/blog/2016/02/29/auto-documented-makefile.html
.PHONY: help

help: ## This help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.DEFAULT_GOAL := help

ifeq ($(shell command -v podman 2> /dev/null),)
	CMD=docker
	ARGS=
else
	BUILDER=podman
	ARGS=--net host
endif

build: ## Build the image.
	$(BUILDER) build $(ARGS) -t $(IMAGE) .

crd: ## Apply the CRD to the current cluster.
	kubectl apply -f $(CRD) --overwrite=true

test: crd  ## Run the tests.
	python test_pguser.py
