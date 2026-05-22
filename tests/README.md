## vhs-decode tests

All tests specific to vhs-decode are contained within this folder

### Unit tests
```sh
$ tests/run_unit.sh
```

### Integration tests

#### Run the tests
```sh
$ tests/run_integration.sh
```

#### Regenerate the expected hashes
* Only run this if you have intentionally made changes that alter the output of vhs-decode
```sh
$ tests/run_integration.sh --regenerate-hashes
```