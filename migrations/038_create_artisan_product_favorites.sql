-- Proposal B: Save showcase products independently of business wishlists.
CREATE TABLE dbo.artisan_product_favorites (
    id INT IDENTITY(1,1) NOT NULL,
    user_id INT NOT NULL,
    product_id INT NOT NULL,
    created_at DATETIME2 NOT NULL
        CONSTRAINT DF_artisan_favorites_created DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_artisan_product_favorites PRIMARY KEY (id),
    CONSTRAINT UQ_artisan_favorites_user_product UNIQUE (user_id, product_id),
    CONSTRAINT FK_artisan_favorites_user
        FOREIGN KEY (user_id) REFERENCES dbo.users(id) ON DELETE CASCADE,
    CONSTRAINT FK_artisan_favorites_product
        FOREIGN KEY (product_id) REFERENCES dbo.showcase_items(id) ON DELETE CASCADE
);
GO

CREATE INDEX IX_artisan_product_favorites_product_id
ON dbo.artisan_product_favorites(product_id);
GO
