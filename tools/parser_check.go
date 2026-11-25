// Maintainer-only harness; compiled inside an isolated, pinned GitStore checkout.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"

	"github.com/gitstore-dev/gitstore/api/internal/validate"
)

func main() {
	var cases []struct{ Path, Name, Title, Parent string }
	if err := json.NewDecoder(os.Stdin).Decode(&cases); err != nil {
		panic(err)
	}
	parser := validate.NewParser().WithRepositoryNamespace(func() (string, error) {
		return "seed-parser-validation", nil
	})
	for _, item := range cases {
		source, err := os.ReadFile(item.Path)
		if err != nil {
			panic(err)
		}
		parsed, body, err := parser.ParseResource(bytes.NewReader(source))
		if err != nil {
			panic(fmt.Sprintf("%s: %v", item.Path, err))
		}
		if parsed == nil || parsed.Kind != "CategoryTaxonomy" || parsed.CategoryTaxonomy == nil {
			panic("not a CategoryTaxonomy: " + item.Path)
		}
		resource := parsed.CategoryTaxonomy
		parent := ""
		if resource.Spec.ParentRef != nil {
			parent = resource.Spec.ParentRef.Name
			if resource.Spec.ParentRef.Namespace != "" {
				panic("unexpected parent namespace: " + item.Path)
			}
		}
		parts := bytes.SplitN(source, []byte("---\n"), 3)
		if resource.Metadata.Name != item.Name || resource.Spec.Title != item.Title ||
			resource.Metadata.Namespace != "seed-parser-validation" || parent != item.Parent ||
			len(resource.Spec.Media) != 0 || len(parts) != 3 || !bytes.Equal(parts[2], body) {
			panic("parser changed category identity, hierarchy or body: " + item.Path)
		}
	}
	fmt.Printf("PASS: %d namespace-free CategoryTaxonomy resources through production parser\n", len(cases))
}
