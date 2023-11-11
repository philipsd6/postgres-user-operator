# postgres-user-operator
A Simple operator for a standalone PostgreSQL database running in a k8s pod.

This operator will handle the creation, updating, and deletion of database users in a standalone PostgreSQL database running in a Kubernetes cluster.

## Requirements

- kubectl and a running Kubernetes cluster.
- a PostgreSQL container running in your current context.

## Deployment

Install the CRD and Sample Deployment with `make install`.

The operator depends on the `POSTGRES_USERNAME` and `POSTGRES_NAMESPACE` environment variables, so customize the `sample-deployment.yaml` for your own use.

## Running tests

Run `make test` to apply the CRD in your current context, and run the test script.

## Contributing

Contributions to this repository are welcome! There are several ways you can contribute:

- Report bugs and suggest enhancements by opening an issue.
- Submit your own code enhancements or bug fixes by opening a pull request.

When contributing, please keep in mind:

- Follow the coding style and conventions used in this repository.
- Write clear commit messages that describe the changes you made.
- Update the test script for your code changes, if necessary
- Make sure the test script passes before submitting your code.

Thank you for your contributions!

## License

This project is licensed under the GNU General Public License v3.0
