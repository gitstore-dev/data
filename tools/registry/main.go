// seed-registry is maintainer/release tooling, never a GitStore service dependency.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"os"
	"strings"
	"time"

	digest "github.com/opencontainers/go-digest"
	oras "github.com/oras-project/oras-go/v3"
	"github.com/oras-project/oras-go/v3/content/oci"
	"github.com/oras-project/oras-go/v3/errdef"
	"github.com/oras-project/oras-go/v3/registry/remote"
	"github.com/oras-project/oras-go/v3/registry/remote/auth"
	"github.com/oras-project/oras-go/v3/registry/remote/credentials"
	"github.com/oras-project/oras-go/v3/registry/remote/retry"
)

func run() error {
	if len(os.Args) < 2 {
		return errors.New("expected probe, publish or pull")
	}
	command := os.Args[1]
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	repository := flags.String("repository", "", "registry/repository without tag")
	tag := flags.String("tag", "", "version tag")
	expected := flags.String("digest", "", "expected SHA-256 manifest digest")
	layout := flags.String("layout", "", "OCI layout directory")
	if err := flags.Parse(os.Args[2:]); err != nil {
		return err
	}
	if command != "probe" && command != "publish" && command != "pull" {
		return errors.New("unsupported command")
	}
	repo, err := remote.NewRepository(*repository)
	if err != nil {
		return err
	}
	host := strings.SplitN(*repository, "/", 2)[0]
	hostname := host
	if value, _, err := net.SplitHostPort(host); err == nil {
		hostname = value
	}
	// Plain HTTP is exclusively for a loopback disposable-registry rehearsal.
	repo.Registry.PlainHTTP = hostname == "localhost" || net.ParseIP(hostname).IsLoopback()
	repo.Registry.MaxMetadataBytes = 1024 * 1024
	repo.Registry.Client = &auth.Client{
		Client: retry.DefaultClient, Cache: auth.NewCache(),
		CredentialFunc: credentials.StaticCredentialFunc(host, credentials.Credential{
			Username: os.Getenv("REGISTRY_USERNAME"), Password: os.Getenv("REGISTRY_TOKEN"),
		}),
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	if command == "probe" {
		desc, err := repo.Resolve(ctx, *tag)
		if errors.Is(err, errdef.ErrNotFound) {
			return json.NewEncoder(os.Stdout).Encode(map[string]any{"exists": false})
		}
		if err != nil {
			return err // Auth, transport and server errors are never absence.
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"exists": true, "digest": desc.Digest.String(), "size": desc.Size})
	}
	if err := digest.Digest(*expected).Validate(); err != nil || !strings.HasPrefix(*expected, "sha256:") {
		return errors.New("expected a valid SHA-256 digest")
	}
	store, err := oci.New(*layout)
	if err != nil {
		return err
	}
	opts := oras.DefaultCopyOptions
	opts.Concurrency = 2
	if command == "pull" {
		desc, err := oras.Copy(ctx, repo, *expected, store, *expected, opts)
		if err != nil {
			return err
		}
		if desc.Digest.String() != *expected {
			return errors.New("pulled manifest digest mismatch")
		}
		return json.NewEncoder(os.Stdout).Encode(desc)
	}
	desc, err := store.Resolve(ctx, *expected)
	if err != nil {
		return err
	}
	if existing, err := repo.Resolve(ctx, *tag); err == nil {
		if existing.Digest != desc.Digest {
			return errors.New("refusing to replace conflicting registry tag")
		}
		return json.NewEncoder(os.Stdout).Encode(existing)
	} else if !errors.Is(err, errdef.ErrNotFound) {
		return err
	}
	// Copy by digest first: interrupted uploads can reuse content-addressed blobs.
	if _, err := oras.Copy(ctx, store, *expected, repo, *expected, opts); err != nil {
		return err
	}
	// Serialize the exclusive producer in Actions and recheck immediately before
	// tagging. OCI/GHCR has no create-only tag CAS; out-of-band writers are forbidden.
	if existing, err := repo.Resolve(ctx, *tag); err == nil {
		if existing.Digest != desc.Digest {
			return errors.New("registry tag changed during upload")
		}
	} else if errors.Is(err, errdef.ErrNotFound) {
		if err := repo.Tag(ctx, desc, *tag); err != nil {
			return err
		}
	} else {
		return err
	}
	actual, err := repo.Resolve(ctx, *tag)
	if err != nil {
		return err
	}
	if actual.Digest != desc.Digest {
		return errors.New("registry tag verification failed")
	}
	return json.NewEncoder(os.Stdout).Encode(actual)
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
