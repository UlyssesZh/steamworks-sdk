# Steamworks SDK

This repository is a mirror of [Steamworks SDK](https://partner.steamgames.com/doc/sdk).
It uses GitHub Actions to check for updates every day and to push to the `sdk` branch of this repository if an update exists.

## File integrity

To ensure that this mirror can be trusted, you need to check the integrity of the files.
[Download](https://partner.steamgames.com/downloads/list) Steamworks SDK manually and check that every file matches.
If you have Nix installed, this can be done conveniently:

```shell
expected_hash=$(nix-prefetch-url "file://$(pwd)/steamworks_sdk_164.zip" --unpack)
actual_hash=$(nix-prefetch-git https://github.com/UlyssesZh/steamworks-sdk.git --rev v1.64 | jq -r .sha256)
if [ $actual_hash = $expected_hash ]; then
	echo "hashes match: $actual_hash == $expected_hash"
else
	echo "hashes mismatch: $actual_hash != $expected_hash"
fi
```

If you do not have Nix installed, you can use diffutils:

```shell
unzip -q steamworks_sdk_164.zip
git clone --branch v1.64 --depth 1 https://github.com/UlyssesZh/steamworks-sdk.git mirror
diff -q -r -x .git sdk mirror
```

Open an issue if the files are not identical.

## Commit hash reproducibility

You can reproduce the full commit history of the `sdk` branch yourself by following this procedure.

1. Clone the repo: `git clone --single-branch --branch master https://github.com/UlyssesZh/steamworks-sdk.git && cd steamworks-sdk`.
2. Set up Steam account: `cp .env.example .env` and then put your Steam account credentials in `.env`.
   Your Steam account must have enabled [Steam Guard](https://help.steampowered.com/en/faqs/view/06B0-26E6-2CF8-254C)
   and accepted [Valve Corporation Steamworks SDK Access Agreement](https://partner.steamgames.com/documentation/sdk_access_agreement).
   To get the Steam Guard shared secret, you may use tools like [steamguard-cli](https://github.com/dyc3/steamguard-cli).
3. Optionally, set up a Python virtual environment by running `python -m venv venv && source venv/bin/activate`.
4. Install dependencies: `pip install -r requirements.txt`.
5. Generate everything: `./generate.py sdk --git-arg=--author="Valve <questions@valvesoftware.com>"`
6. Check the commit hashes:

```shell
actual_hash=$(git -C sdk rev-parse HEAD)
expected_hash=$(curl -s "https://api.github.com/repos/UlyssesZh/steamworks-sdk/commits?per_page=1" | jq -r '.[0].sha')
if [ $actual_hash = $expected_hash ]; then
	echo "hashes match: $actual_hash == $expected_hash"
else
	echo "hashes mismatch: $actual_hash != $expected_hash"
fi
```

If you cannot reproduce the commit hash, check the following:

- Environment variables `GIT_COMMITTER_NAME` and `GIT_COMMITTER_EMAIL` are correctly set.
- If you have GPG signing configured in Git global config, add the option `--git-commit-arg=--no-gpg-sign` to the `./generate.py` call.
- If you have hooks configured in Git global config, add the options `--git-commit-arg=--no-post-rewrite --git-commit-arg=--no-verify`.

Open an issue if the commit hash really cannot be reproduced.

## License

Files in the `master` branch are licensed under the MIT license.
Files in the `sdk` branch are licensed under
[Valve Corporation Steamworks SDK Access Agreement](https://partner.steamgames.com/documentation/sdk_access_agreement).
