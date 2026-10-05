import { contactCreateSchema, MATCH_TYPES, TIERS, type Contact, type Tier } from "@commitmail/shared";
import { Pencil, Plus, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Avatar, PageHeader, TierBadge } from "../components/domain";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Field, Input, Select } from "../components/ui/form";
import { ConfirmDialog, Dialog } from "../components/ui/overlay";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/states";
import { errorMessage } from "../lib/api";
import { useContacts, useDeleteContact, useSaveContact } from "../lib/queries";

const MATCH_LABELS: Record<string, { label: string; placeholder: string }> = {
  exact_email: { label: "Exact address", placeholder: "boss@company.com" },
  domain: { label: "Whole domain", placeholder: "company.com" },
  name_pattern: { label: "Name contains", placeholder: "Priya" },
};

const TIER_HELP: Record<Tier, string> = {
  CRITICAL: "Read by the model; commitments go straight to your calendar.",
  IMPORTANT: "Read by the model; commitments go to your calendar, flagged for a look.",
  MONITOR: "Read by the model; commitments wait for your approval.",
  SKIP: "Never sent to the model.",
};

export default function Contacts() {
  const contacts = useContacts();
  const remove = useDeleteContact();
  const [editing, setEditing] = useState<Contact | "new" | null>(null);
  const [deleting, setDeleting] = useState<Contact | null>(null);

  return (
    <>
      <PageHeader
        title="Contacts"
        description="Whose email the model reads, and how urgently its commitments reach your calendar. Unknown senders are skipped."
        actions={<Button variant="primary" onClick={() => setEditing("new")}><Plus className="size-4" /> Add rule</Button>}
      />

      <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {TIERS.map((tier) => (
          <Card key={tier} className="p-4">
            <div className="flex items-center justify-between">
              <TierBadge tier={tier} />
              <span className="text-lg font-semibold tabular-nums">{contacts.data?.filter((c) => c.tier === tier).length ?? "—"}</span>
            </div>
            <p className="mt-2 text-[12.5px] text-muted">{TIER_HELP[tier]}</p>
          </Card>
        ))}
      </div>

      <Card className="overflow-hidden">
        {contacts.isPending ? (
          <div className="p-5"><SkeletonRows rows={5} /></div>
        ) : contacts.error ? (
          <ErrorState error={contacts.error} onRetry={() => void contacts.refetch()} />
        ) : !contacts.data?.length ? (
          <EmptyState icon={<Users />} title="No rules yet" description="Add the people and domains whose email matters. Everything else is skipped." action={<Button onClick={() => setEditing("new")}>Add your first rule</Button>} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13.5px]">
              <thead className="border-b border-border bg-surface-2 text-[12px] text-muted">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Contact</th>
                  <th className="px-4 py-2.5 font-medium">Matches</th>
                  <th className="px-4 py-2.5 font-medium">Tier</th>
                  <th className="px-4 py-2.5 text-right font-medium">Emails</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {contacts.data.map((contact) => (
                  <tr key={contact.id} className="group transition-colors hover:bg-surface-2/60">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <Avatar name={contact.displayName ?? contact.matchValue} email={contact.matchValue} size={30} />
                        <span className="font-medium text-text">{contact.displayName || contact.matchValue}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-muted">
                      <span className="text-faint">{MATCH_LABELS[contact.matchType]?.label}:</span> <span className="font-mono text-[12.5px]">{contact.matchValue}</span>
                    </td>
                    <td className="px-4 py-3"><TierBadge tier={contact.tier} /></td>
                    <td className="px-4 py-3 text-right text-muted tabular-nums">{contact.matchingEmails}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1 opacity-60 transition-opacity group-hover:opacity-100">
                        <Button size="icon-sm" variant="ghost" aria-label={`Edit ${contact.matchValue}`} onClick={() => setEditing(contact)}><Pencil className="size-3.5" /></Button>
                        <Button size="icon-sm" variant="ghost" aria-label={`Delete ${contact.matchValue}`} onClick={() => setDeleting(contact)}><Trash2 className="size-3.5" /></Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {editing && <ContactDialog contact={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Remove this rule?"
        description={`Mail from ${deleting?.matchValue} will be treated as an unknown sender again. Stored mail is re-sorted.`}
        confirmLabel="Remove rule"
        loading={remove.isPending}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting.id, {
            onSuccess: () => {
              toast.success("Rule removed — re-sorting stored mail");
              setDeleting(null);
            },
            onError: (error) => toast.error(errorMessage(error)),
          })
        }
      />
    </>
  );
}

function ContactDialog({ contact, onClose }: { contact: Contact | null; onClose: () => void }) {
  const save = useSaveContact();
  const [form, setForm] = useState({
    matchType: contact?.matchType ?? "exact_email",
    matchValue: contact?.matchValue ?? "",
    tier: contact?.tier ?? "IMPORTANT",
    displayName: contact?.displayName ?? "",
  });
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    if (!contact) {
      const parsed = contactCreateSchema.safeParse({ ...form, displayName: form.displayName || undefined });
      if (!parsed.success) {
        setError(parsed.error.issues[0]?.message === "not an email address" ? "That isn't an email address." : parsed.error.issues[0]?.message === "not a domain" ? "That isn't a domain." : "Check the fields.");
        return;
      }
    }
    const body = contact ? { tier: form.tier, displayName: form.displayName || null } : { ...form, displayName: form.displayName || undefined };
    save.mutate(
      { id: contact?.id, body },
      {
        onSuccess: () => {
          toast.success(contact ? "Rule updated — re-sorting stored mail" : "Rule added — re-sorting stored mail");
          onClose();
        },
        onError: (e) => setError(errorMessage(e)),
      },
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={contact ? "Edit rule" : "Add a contact rule"}
      description="Rules apply to new mail and to everything already stored."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={save.isPending} onClick={submit}>{contact ? "Save" : "Add rule"}</Button>
        </>
      }
    >
      <div className="space-y-4">
        {!contact && (
          <div className="grid grid-cols-[auto_1fr] gap-2">
            <Select aria-label="Match type" value={form.matchType} onChange={(e) => setForm({ ...form, matchType: e.target.value as typeof form.matchType })}>
              {MATCH_TYPES.map((t) => <option key={t} value={t}>{MATCH_LABELS[t]!.label}</option>)}
            </Select>
            <Input aria-label="Match value" value={form.matchValue} placeholder={MATCH_LABELS[form.matchType]!.placeholder} onChange={(e) => setForm({ ...form, matchValue: e.target.value })} autoFocus />
          </div>
        )}
        <Field label="Display name" hint="Optional — how this contact is labelled.">
          {(props) => <Input {...props} value={form.displayName} onChange={(e) => setForm({ ...form, displayName: e.target.value })} />}
        </Field>
        <Field label="Tier" hint={TIER_HELP[form.tier]}>
          {(props) => (
            <Select {...props} value={form.tier} onChange={(e) => setForm({ ...form, tier: e.target.value as Tier })}>
              {TIERS.map((t) => <option key={t} value={t}>{t.charAt(0) + t.slice(1).toLowerCase()}</option>)}
            </Select>
          )}
        </Field>
        {error && <p role="alert" className="text-[13px] text-danger">{error}</p>}
      </div>
    </Dialog>
  );
}
