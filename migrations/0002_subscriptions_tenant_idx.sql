-- /checkout and every subscription webhook look up a tenant's subscriptions; without this they scan the table.
CREATE INDEX subscriptions_tenant ON subscriptions (tenant_id);
